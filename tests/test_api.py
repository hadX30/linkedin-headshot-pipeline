import dataclasses
import time

import pytest
from fastapi.testclient import TestClient

from headshot.api.app import create_app

from .conftest import make_photo

ADMIN = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def client(settings, store):
    with TestClient(create_app(settings, store)) as c:
        yield c


def upload(client, data, *, path="/v1/runs", name="me.jpg", form=None, headers=None):
    return client.post(path, files={"image": (name, data, "image/jpeg")}, data=form or {}, headers=headers or {})


def wait(client, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/v1/runs/{run_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# ── the original API, unchanged ───────────────────────────────────────────────

def test_health_is_the_original(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_original_endpoint_runs_synchronously_and_returns_the_original_shape(client, photo):
    res = upload(client, photo, path="/v1/headshots")
    assert res.status_code == 201
    body = res.json()
    assert set(body) == {"job_id", "status", "analysis", "prompt", "generated_image", "validation", "retries", "error"}
    assert body["status"] in ("delivered", "manual_review")
    assert body["generated_image"] == f"/v1/headshots/{body['job_id']}/image"
    img = client.get(body["generated_image"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.headers["content-disposition"] == 'attachment; filename="headshot.png"'


def test_original_endpoint_rejects_unsupported_extensions(client, photo):
    res = upload(client, photo, path="/v1/headshots", name="photo.gif")
    assert res.status_code == 415
    assert res.json() == {"detail": "Unsupported image type. Upload a JPEG, PNG, or WebP image."}


def test_original_endpoint_returns_500_with_the_result_on_error(settings, store, photo):
    failing = dataclasses.replace(settings, mock_scenario="generation_error")
    with TestClient(create_app(failing, store)) as c:
        res = upload(c, photo, path="/v1/headshots")
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail["status"] == "error" and detail["error"].startswith("Stage 3 failed (attempt 3)")


def test_original_image_endpoint_serves_manual_review_output(settings, store, photo):
    manual = dataclasses.replace(settings, mock_scenario="manual_review")
    with TestClient(create_app(manual, store)) as c:
        body = upload(c, photo, path="/v1/headshots").json()
        assert body["status"] == "manual_review"
        assert c.get(f"/v1/headshots/{body['job_id']}/image").status_code == 200  # as before


def test_original_image_endpoint_404s(client):
    assert client.get("/v1/headshots/not-a-uuid/image").json() == {"detail": "Headshot not found"}
    missing = client.get("/v1/headshots/00000000-0000-0000-0000-000000000000/image")
    assert missing.status_code == 404 and missing.json() == {"detail": "Generated headshot not found"}


# ── the new background endpoint (separate) ────────────────────────────────────

def test_config(client):
    cfg = client.get("/v1/config").json()
    assert cfg["mode"] == "mock" and cfg["max_attempts"] == 3 and cfg["thresholds"]["face_match"] == 85


def test_new_endpoint_upload_poll_and_fetch(client, photo):
    res = upload(client, photo, form={"scenario": "retry_then_deliver"})
    assert res.status_code == 202
    body = wait(client, res.json()["run_id"])
    assert body["status"] == "delivered" and body["reason"] == "passed"
    assert [r["action"] for r in body["reviews"]] == ["retry", "deliver"]
    assert client.get(body["image_url"]).status_code == 200


def test_new_endpoint_reports_analyzer_rejection_as_manual_review(client, photo):
    body = wait(client, upload(client, photo, form={"scenario": "rejected"}).json()["run_id"])
    assert body["status"] == "manual_review" and body["reason"] == "analyzer_rejected"
    assert body["analysis"]["decision"]["reject_reason"] == "multiple_faces"
    assert body["image_url"] is None


def test_new_endpoint_hides_internal_data(client, photo):
    body = wait(client, upload(client, photo, form={"scenario": "deliver"}).json()["run_id"])
    text = str(body)
    for secret in ("system", "prompt", "cost", "apparent_gender", "skin_tone", "apparent_age_range", "guidance"):
        assert secret not in text, secret


def test_new_endpoint_rejects_bad_uploads(client, settings):
    assert upload(client, b"hello").status_code == 415
    assert upload(client, make_photo(), name="x.gif").status_code == 415
    big = b"\0" * (settings.max_upload_mb * 1024 * 1024 + 1)
    assert upload(client, big).status_code == 413
    assert upload(client, make_photo(), form={"scenario": "nope"}).status_code == 400


def test_new_endpoint_needs_keys_in_live_mode(settings, store, photo):
    with TestClient(create_app(dataclasses.replace(settings, mode="live"), store)) as c:
        assert upload(c, photo).status_code == 503


def test_rate_limit(settings, store, photo):
    with TestClient(create_app(dataclasses.replace(settings, rate_limit_per_hour=2), store)) as c:
        assert upload(c, photo).status_code == 202
        assert upload(c, photo).status_code == 202
        res = upload(c, photo)
        assert res.status_code == 429 and "retry-after" in res.headers


def test_cors(client, settings, store):
    ok = client.options("/v1/runs", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/v1/runs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
    preview = dataclasses.replace(settings, cors_origin_regex=r"https://malameh-site(-[a-z0-9-]+)?\.vercel\.app")
    with TestClient(create_app(preview, store)) as c:
        def allowed(origin):
            res = c.options("/v1/runs", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
            return res.headers.get("access-control-allow-origin") == origin
        assert allowed("https://malameh-site-git-feature-x-ajlans-projects.vercel.app")
        assert not allowed("https://malameh-site.vercel.app.evil.example")


# ── admin ─────────────────────────────────────────────────────────────────────

def test_admin_requires_token(client, settings, store):
    assert client.get("/admin/api/overview").status_code == 401
    with TestClient(create_app(dataclasses.replace(settings, admin_token=""), store)) as c:
        assert c.get("/admin/api/overview", headers=ADMIN).status_code == 503
    assert client.get("/admin").status_code == 200


def test_admin_run_detail_and_files(client, photo):
    run_id = upload(client, photo, form={"scenario": "retry_then_deliver"}).json()["run_id"]
    wait(client, run_id)
    detail = client.get(f"/admin/api/runs/{run_id}", headers=ADMIN).json()
    assert detail["run"]["status"] == "delivered" and len(detail["spans"]) == 7
    assert detail["run"]["result"]["pipeline"]["retries"] == 1
    assert client.get(f"/admin/api/runs/{run_id}/files/input.jpg", headers=ADMIN).content == photo
    assert client.get(f"/admin/api/runs/{run_id}/files/attempt-1.png", headers=ADMIN).status_code == 200
    assert client.get(f"/admin/api/runs/{run_id}/files/../../etc", headers=ADMIN).status_code == 404


def test_admin_overview_mode_switch_and_delete(client, photo):
    run_id = upload(client, photo, form={"scenario": "deliver"}).json()["run_id"]
    wait(client, run_id)
    o = client.get("/admin/api/overview?days=7", headers=ADMIN).json()
    assert o["stats"]["runs"] == 1 and o["proposals"] == {"review_fail_safe": False, "image_send_input_fidelity": False}
    assert client.put("/admin/api/settings", json={"mode": "live"}, headers=ADMIN).status_code == 409
    assert client.delete(f"/admin/api/runs/{run_id}", headers=ADMIN).status_code == 204
    assert client.get(f"/v1/runs/{run_id}").status_code == 404
