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


# ── the pipeline's API (the team's api.py contract) ───────────────────────────

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_headshots_endpoint_runs_synchronously_and_returns_the_pipeline_result(settings, store, photo):
    beta = dataclasses.replace(settings, mock_scenario="floor_then_deliver")
    with TestClient(create_app(beta, store)) as c:
        res = upload(c, photo, path="/v1/headshots")
        assert res.status_code == 201
        body = res.json()
        assert set(body) == {"job_id", "status", "analysis", "prompt", "generated_image", "validation", "retries",
                             "error", "selected_attempt", "candidates_generated", "candidate_scores"}
        assert (body["status"], body["selected_attempt"], body["candidates_generated"], body["retries"]) == \
            ("delivered", 2, 2, 1)
        assert [c["composite"] for c in body["candidate_scores"]] == [82.0, 87.5]
        assert body["generated_image"] == f"/v1/headshots/{body['job_id']}/image"
        img = c.get(body["generated_image"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        assert img.headers["content-disposition"] == 'attachment; filename="headshot.png"'
        # the selected candidate, not just the last file written
        assert img.content == store.get_file(body["job_id"], "attempt-2.png")


def test_headshots_endpoint_rejects_unsupported_extensions(client, photo):
    res = upload(client, photo, path="/v1/headshots", name="photo.gif")
    assert res.status_code == 415
    assert res.json() == {"detail": "Unsupported image type. Upload a JPEG, PNG, or WebP image."}


def test_headshots_endpoint_returns_500_with_the_result_on_error(settings, store, photo):
    failing = dataclasses.replace(settings, mock_scenario="generation_error")
    with TestClient(create_app(failing, store)) as c:
        res = upload(c, photo, path="/v1/headshots")
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail["status"] == "error" and detail["error"].startswith("Stage 3 failed on every candidate")


def test_headshots_image_serves_the_best_candidate_on_manual_review(settings, store, photo):
    weak = dataclasses.replace(settings, mock_scenario="no_deliverable")
    with TestClient(create_app(weak, store)) as c:
        body = upload(c, photo, path="/v1/headshots").json()
        assert body["status"] == "manual_review" and body["selected_attempt"] == 2
        img = c.get(f"/v1/headshots/{body['job_id']}/image")
        assert img.status_code == 200 and img.content == store.get_file(body["job_id"], "attempt-2.png")


def test_headshots_image_404s(client):
    assert client.get("/v1/headshots/not-a-uuid/image").json() == {"detail": "Headshot not found"}
    missing = client.get("/v1/headshots/00000000-0000-0000-0000-000000000000/image")
    assert missing.status_code == 404 and missing.json() == {"detail": "Generated headshot not found"}


# ── for the website: background runs ──────────────────────────────────────────

def test_config(client):
    cfg = client.get("/v1/config").json()
    assert cfg["mode"] == "mock" and cfg["max_attempts"] == 3 and cfg["thresholds"]["background"] == 80
    assert cfg["decision"] == {
        "floors": {"face_match": 78, "attire_accuracy": 80, "background": 65},
        "weights": {"face_match": 0.35, "attire_accuracy": 0.30, "realism": 0.20, "background": 0.15},
        "deliver_at": 82,
        "max_candidates": 3,
    }


def test_runs_endpoint_upload_poll_and_fetch(client, photo):
    res = upload(client, photo, form={"scenario": "floor_then_deliver"})
    assert res.status_code == 202
    body = wait(client, res.json()["run_id"])
    assert body["status"] == "delivered" and body["reason"] == "deliverable" and body["selected_attempt"] == 2
    first, second = body["reviews"]
    assert (first["action"], first["composite"], first["deliverable"], first["selected"]) == ("retry", 82.0, False, False)
    assert first["checks"]["background"] | {"issues_ar": None} == {
        "score": 58, "floor": 65, "weight": 0.15, "below_floor": True, "threshold": 80, "passed": False, "issues_ar": None}
    assert (second["action"], second["deliverable"], second["selected"]) == ("deliver", True, True)
    assert client.get(body["image_url"]).status_code == 200


def test_runs_endpoint_reports_failed_candidates(client, photo):
    body = wait(client, upload(client, photo, form={"scenario": "generation_failure"}).json()["run_id"])
    assert [(r["attempt"], r["action"]) for r in body["reviews"]] == [(1, "generation_failed"), (2, "deliver")]


def test_runs_endpoint_reports_analyzer_rejection_as_manual_review(client, photo):
    body = wait(client, upload(client, photo, form={"scenario": "rejected"}).json()["run_id"])
    assert body["status"] == "manual_review" and body["reason"] == "analyzer_rejected"
    assert body["analysis"]["decision"]["reject_reason"] == "multiple_faces"
    assert body["image_url"] is None


def test_runs_endpoint_hides_internal_data(client, photo):
    body = wait(client, upload(client, photo, form={"scenario": "floor_then_deliver"}).json()["run_id"])
    text = str(body)
    for secret in ("system", "prompt", "cost", "apparent_gender", "skin_tone", "apparent_age_range", "guidance",
                   "café", "issues':"):
        assert secret not in text, secret


def test_runs_endpoint_rejects_bad_uploads(client, settings):
    assert upload(client, b"hello").status_code == 415
    assert upload(client, make_photo(), name="x.gif").status_code == 415
    big = b"\0" * (settings.max_upload_mb * 1024 * 1024 + 1)
    assert upload(client, big).status_code == 413
    assert upload(client, make_photo(), form={"scenario": "nope"}).status_code == 400


def test_runs_endpoint_needs_keys_in_live_mode(settings, store, photo):
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
    run_id = upload(client, photo, form={"scenario": "floor_then_deliver"}).json()["run_id"]
    wait(client, run_id)
    detail = client.get(f"/admin/api/runs/{run_id}", headers=ADMIN).json()
    assert detail["run"]["status"] == "delivered" and len(detail["spans"]) == 9
    assert detail["run"]["result"]["pipeline"]["retries"] == 1
    assert [c["selected"] for c in detail["run"]["result"]["candidates"]] == [False, True]
    assert client.get(f"/admin/api/runs/{run_id}/files/input.jpg", headers=ADMIN).content == photo
    assert client.get(f"/admin/api/runs/{run_id}/files/attempt-1.png", headers=ADMIN).status_code == 200
    assert client.get(f"/admin/api/runs/{run_id}/files/../../etc", headers=ADMIN).status_code == 404


def test_admin_overview_mode_switch_and_delete(client, photo):
    run_id = upload(client, photo, form={"scenario": "deliver"}).json()["run_id"]
    wait(client, run_id)
    o = client.get("/admin/api/overview?days=7", headers=ADMIN).json()
    assert o["stats"]["runs"] == 1 and o["rules"]["deliver_at"] == 82 and o["rules"]["max_candidates"] == 3
    assert o["models"]["llm"] and o["models"]["image"]
    assert {s["name"] for s in client.get("/admin/api/scenarios", headers=ADMIN).json()} >= {"auto", "deliver"}
    assert client.put("/admin/api/settings", json={"mode": "live"}, headers=ADMIN).status_code == 409
    assert client.delete(f"/admin/api/runs/{run_id}", headers=ADMIN).status_code == 204
    assert client.get(f"/v1/runs/{run_id}").status_code == 404
