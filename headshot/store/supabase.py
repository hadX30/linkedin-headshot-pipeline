"""
Hosted trace store: Supabase Postgres (via PostgREST) and Supabase Storage.

Uses the project's secret key server-side only. Tables and the private bucket
come from supabase/migrations/20261020120000_traces.sql.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .base import utcnow


class SupabaseError(RuntimeError):
    pass


class SupabaseStore:
    kind = "supabase"

    def __init__(self, url: str, secret_key: str, *, schema: str = "public", bucket: str = "malameh-headshots",
                 transport: httpx.BaseTransport | None = None):
        headers = {"apikey": secret_key}
        if not secret_key.startswith("sb_"):
            # Legacy service_role JWT keys also go in Authorization. New sb_secret_ keys
            # must not: the gateway derives the Authorization header from apikey itself.
            headers["Authorization"] = f"Bearer {secret_key}"
        if schema != "public":
            headers["Accept-Profile"] = schema
            headers["Content-Profile"] = schema
        self.bucket = bucket
        self.http = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=30.0, transport=transport)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            raise SupabaseError(f"Supabase {response.request.method} {response.request.url.path} "
                                f"failed with {response.status_code}: {response.text[:300]}")
        return response

    def _rest(self, method: str, table: str, *, params: dict | None = None, json: Any = None,
              prefer: str | None = None) -> httpx.Response:
        headers = {"Prefer": prefer} if prefer else None
        return self._check(self.http.request(method, f"/rest/v1/{table}", params=params, json=json, headers=headers))

    def _object_path(self, run_id: str, name: str) -> str:
        return f"/storage/v1/object/{self.bucket}/runs/{quote(run_id)}/{quote(name)}"

    # ── runs ──────────────────────────────────────────────────────────────────

    def create_run(self, run: dict[str, Any]) -> None:
        self._rest("POST", "runs", json=run, prefer="return=minimal")

    def update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        self._rest("PATCH", "runs", params={"id": f"eq.{run_id}"}, json={**fields, "updated_at": utcnow()},
                   prefer="return=minimal")

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._rest("GET", "runs", params={"id": f"eq.{run_id}", "select": "*"}).json()
        return _normalise_run(rows[0]) if rows else None

    def list_runs(self, *, limit: int = 50, offset: int = 0, status: str | None = None, mode: str | None = None):
        params = {"select": "*", "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
        if status:
            params["status"] = f"eq.{status}"
        if mode:
            params["mode"] = f"eq.{mode}"
        response = self._rest("GET", "runs", params=params, prefer="count=exact")
        content_range = response.headers.get("content-range", "*/0")
        total = int(content_range.rsplit("/", 1)[-1]) if content_range.rsplit("/", 1)[-1].isdigit() else 0
        return [_normalise_run(r) for r in response.json()], total

    def delete_run(self, run_id: str) -> None:
        names = [f"runs/{run_id}/{item['name']}" for item in self._list_objects(run_id)]
        if names:
            self._check(self.http.request("DELETE", f"/storage/v1/object/{self.bucket}", json={"prefixes": names}))
        self._rest("DELETE", "runs", params={"id": f"eq.{run_id}"}, prefer="return=minimal")

    def fail_unfinished(self, reason: str) -> int:
        now = utcnow()
        response = self._rest(
            "PATCH", "runs",
            params={"status": "in.(queued,running)", "select": "id"},
            json={"status": "error", "error": reason, "finished_at": now, "updated_at": now},
            prefer="return=representation",
        )
        return len(response.json())

    # ── spans ─────────────────────────────────────────────────────────────────

    def add_span(self, span: dict[str, Any]) -> None:
        self._rest("POST", "spans", json=span, prefer="return=minimal")

    def list_spans(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._rest("GET", "spans", params={"run_id": f"eq.{run_id}", "select": "*", "order": "seq.asc"}).json()
        for row in rows:
            row["cost_usd"] = float(row["cost_usd"]) if row.get("cost_usd") is not None else None
        return rows

    # ── files ─────────────────────────────────────────────────────────────────

    def put_file(self, run_id: str, name: str, data: bytes, content_type: str) -> None:
        self._check(self.http.post(self._object_path(run_id, name), content=data,
                                   headers={"Content-Type": content_type, "x-upsert": "true"}))

    def get_file(self, run_id: str, name: str) -> bytes | None:
        response = self.http.get(self._object_path(run_id, name))
        if response.status_code in (400, 404):
            return None
        return self._check(response).content

    def _list_objects(self, run_id: str) -> list[dict[str, Any]]:
        # Storage lists a "folder" by its path without a trailing slash (as supabase-js sends it).
        response = self.http.post(f"/storage/v1/object/list/{self.bucket}",
                                  json={"prefix": f"runs/{run_id}", "limit": 100, "offset": 0})
        return self._check(response).json()

    # ── settings ──────────────────────────────────────────────────────────────

    def get_setting(self, key: str) -> Any:
        rows = self._rest("GET", "settings", params={"key": f"eq.{key}", "select": "value"}).json()
        return rows[0]["value"] if rows else None

    def set_setting(self, key: str, value: Any) -> None:
        self._rest("POST", "settings", params={"on_conflict": "key"},
                   json={"key": key, "value": value, "updated_at": utcnow()},
                   prefer="resolution=merge-duplicates,return=minimal")

    # ── reporting ─────────────────────────────────────────────────────────────

    def stats(self, since: str) -> dict[str, Any]:
        response = self.http.post("/rest/v1/rpc/trace_stats", json={"since": since})
        return self._check(response).json()


def _normalise_run(row: dict[str, Any]) -> dict[str, Any]:
    # numeric columns arrive as strings or numbers depending on PostgREST settings
    if row.get("cost_usd") is not None:
        row["cost_usd"] = float(row["cost_usd"])
    return row
