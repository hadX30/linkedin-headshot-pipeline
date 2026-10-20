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


def upload(client, data, *, scenario=None, name="me.jpg", path="/v1/runs", headers=None, extra=None):
    form = {"scenario": scenario} if scenario else {}
    form.update(extra or {})
    return client.post(path, files={"image": (name, data, "image/jpeg")}, data=form, headers=headers or {})


def wait(client, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/v1/runs/{run_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_health_and_config(client):
    assert client.get("/health").json() == {"status": "ok", "mode": "mock", "store": "sqlite"}
    cfg = client.get("/v1/config").json()
    assert cfg["mode"] == "mock" and cfg["max_attempts"] == 3 and cfg["thresholds"]["face_match"] == 85


def test_upload_poll_and_fetch_delivered_image(client, photo):
    res = upload(client, photo, scenario="retry_then_deliver")
    assert res.status_code == 202
    body = wait(client, res.json()["run_id"])
    assert body["status"] == "delivered"
    assert [r["action"] for r in body["reviews"]] == ["retry", "deliver"]
    assert body["analysis"]["headwear"]["type"]
    img = client.get(body["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


def test_public_view_hides_internal_data(client, photo):
    body = wait(client, upload(client, photo, scenario="deliver").json()["run_id"])
    text = str(body)
    for secret in ("system", "prompt", "cost", "apparent_gender", "skin_tone", "apparent_age_range", "guidance"):
        assert secret not in text, secret


def test_manual_review_image_is_not_public(client, photo):
    body = wait(client, upload(client, photo, scenario="manual_review").json()["run_id"])
    assert body["status"] == "manual_review" and body["image_url"] is None
    assert client.get(f"/v1/runs/{body['id']}/image").status_code == 404


def test_rejects_bad_uploads(client, settings):
    assert upload(client, b"hello", name="x.jpg").status_code == 415
    assert upload(client, make_photo(size=(200, 200))).status_code == 422
    big = b"\0" * (settings.max_upload_mb * 1024 * 1024 + 1)
    assert upload(client, big).status_code == 413
    assert upload(client, make_photo(), scenario="nope").status_code == 400


def test_unknown_run_is_404(client):
    assert client.get("/v1/runs/not-a-uuid").status_code == 404
    assert client.get("/v1/runs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_rate_limit(settings, store, photo):
    tight = dataclasses.replace(settings, rate_limit_per_hour=2)
    with TestClient(create_app(tight, store)) as c:
        assert upload(c, photo).status_code == 202
        assert upload(c, photo).status_code == 202
        res = upload(c, photo)
        assert res.status_code == 429 and "retry-after" in res.headers


def test_cors_allows_only_configured_origin(client):
    ok = client.options("/v1/runs", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/v1/runs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


# ── admin ─────────────────────────────────────────────────────────────────────

def test_admin_requires_token(client, settings, store):
    assert client.get("/admin/api/overview").status_code == 401
    assert client.get("/admin/api/overview", headers={"Authorization": "Bearer wrong"}).status_code == 401
    with TestClient(create_app(dataclasses.replace(settings, admin_token=""), store)) as c:
        assert c.get("/admin/api/overview", headers=ADMIN).status_code == 503
    assert client.get("/admin").status_code == 200  # the static shell holds no data


def test_admin_trace_detail_has_everything(client, photo):
    run_id = upload(client, photo, scenario="retry_then_deliver").json()["run_id"]
    wait(client, run_id)
    detail = client.get(f"/admin/api/runs/{run_id}", headers=ADMIN).json()
    assert detail["run"]["status"] == "delivered"
    assert len(detail["spans"]) == 7
    first = detail["spans"][0]
    assert first["request"]["system"] and first["usage"]["input_tokens"] > 0 and first["cost_usd"] > 0
    img = client.get(f"/admin/api/runs/{run_id}/files/attempt-1.png", headers=ADMIN)
    assert img.status_code == 200  # failed attempts stay visible to admins
    assert client.get(f"/admin/api/runs/{run_id}/files/../../etc", headers=ADMIN).status_code == 404


def test_admin_overview_and_listing(client, photo):
    wait(client, upload(client, photo, scenario="deliver").json()["run_id"])
    o = client.get("/admin/api/overview?days=7", headers=ADMIN).json()
    assert o["mode"] == "mock" and o["live_available"] is False and o["stats"]["runs"] == 1
    listing = client.get("/admin/api/runs?status=delivered", headers=ADMIN).json()
    assert listing["total"] == 1 and "request" not in listing["runs"][0]


def test_mode_switch(client):
    res = client.put("/admin/api/settings", json={"mode": "live"}, headers=ADMIN)
    assert res.status_code == 409  # no keys configured
    assert client.put("/admin/api/settings", json={"mode": "mock"}, headers=ADMIN).json() == {"mode": "mock"}


def test_admin_test_run_and_delete(client, photo):
    res = upload(client, photo, scenario="exhausted", path="/admin/api/runs", headers=ADMIN, extra={"mode": "mock"})
    assert res.status_code == 202
    run_id = res.json()["run_id"]
    assert wait(client, run_id)["status"] == "manual_review"
    assert client.delete(f"/admin/api/runs/{run_id}", headers=ADMIN).status_code == 204
    assert client.get(f"/v1/runs/{run_id}").status_code == 404


def test_cors_origin_regex_allows_preview_deployments(settings, store):
    preview = dataclasses.replace(settings, cors_origin_regex=r"https://malameh-site(-[a-z0-9-]+)?\.vercel\.app")
    with TestClient(create_app(preview, store)) as c:
        def allowed(origin):
            res = c.options("/v1/runs", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
            return res.headers.get("access-control-allow-origin") == origin
        assert allowed("https://malameh-site.vercel.app")
        assert allowed("https://malameh-site-git-feature-vite-restructure-ajlan0.vercel.app")
        assert not allowed("https://malameh-site.vercel.app.evil.example")
        assert not allowed("https://evil.vercel.app")
