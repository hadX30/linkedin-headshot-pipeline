"""The Supabase store against a fake HTTP transport: headers, paths and parsing."""

import json

import httpx

from headshot.store.supabase import SupabaseStore


def make(key="sb_secret_abc"):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/rest/v1/runs" and request.method == "GET" and "count=exact" in request.headers.get("prefer", ""):
            return httpx.Response(200, json=[{"id": "r1", "cost_usd": "0.120000"}], headers={"content-range": "0-0/42"})
        if path == "/rest/v1/runs" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "r1", "cost_usd": "0.5"}])
        if path == "/rest/v1/rpc/trace_stats":
            return httpx.Response(200, json={"runs": 3})
        if path.startswith("/storage/v1/object/list/"):
            return httpx.Response(200, json=[{"name": "input.jpg"}, {"name": "attempt-1.png"}])
        if path.startswith("/storage/v1/object/") and request.method == "GET":
            return httpx.Response(404, json={"error": "not found"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201 if request.method == "POST" else 204)

    store = SupabaseStore("https://proj.supabase.co", key, transport=httpx.MockTransport(handler))
    return store, calls


def test_secret_key_goes_in_apikey_only():
    store, calls = make("sb_secret_abc")
    store.create_run({"id": "r1"})
    assert calls[0].headers["apikey"] == "sb_secret_abc"
    assert "authorization" not in calls[0].headers


def test_legacy_jwt_key_also_sent_as_bearer():
    store, calls = make("eyJhbGciOi.legacy")
    store.get_setting("mode")
    assert calls[0].headers["authorization"] == "Bearer eyJhbGciOi.legacy"


def test_list_runs_reads_total_from_content_range():
    store, _ = make()
    rows, total = store.list_runs(limit=1, status="delivered")
    assert total == 42 and rows[0]["cost_usd"] == 0.12


def test_update_and_upsert_shapes():
    store, calls = make()
    store.update_run("r1", {"status": "running"})
    store.set_setting("mode", "live")
    update, upsert = calls
    assert update.method == "PATCH" and update.url.params["id"] == "eq.r1"
    assert json.loads(update.content)["status"] == "running"
    assert upsert.url.params["on_conflict"] == "key"
    assert "merge-duplicates" in upsert.headers["prefer"]


def test_files_and_delete():
    store, calls = make()
    store.put_file("r1", "input.jpg", b"jpeg", "image/jpeg")
    assert calls[-1].url.path == "/storage/v1/object/malameh-headshots/runs/r1/input.jpg"
    assert calls[-1].headers["x-upsert"] == "true"
    assert store.get_file("r1", "missing.png") is None
    store.delete_run("r1")
    removal = next(c for c in calls if c.method == "DELETE" and "storage" in c.url.path)
    assert json.loads(removal.content)["prefixes"] == ["runs/r1/input.jpg", "runs/r1/attempt-1.png"]
    assert calls[-1].method == "DELETE" and calls[-1].url.path == "/rest/v1/runs"


def test_stats_calls_rpc():
    store, calls = make()
    assert store.stats("2026-01-01") == {"runs": 3}
    assert calls[0].url.path == "/rest/v1/rpc/trace_stats"
