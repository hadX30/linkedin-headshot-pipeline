"""Local trace store: one SQLite file plus a folder of images per run."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .base import FINISHED_STATUSES, utcnow

RUN_JSON = ("input", "analysis", "result", "models")
SPAN_JSON = ("request", "response", "usage")
RUN_BOOL = ("cost_estimated",)
SPAN_BOOL = ("cost_estimated",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    finished_at    TEXT,
    mode           TEXT NOT NULL CHECK (mode IN ('mock', 'live')),
    scenario       TEXT,
    source         TEXT NOT NULL,
    status         TEXT NOT NULL,
    stage          TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    error          TEXT,
    input          TEXT,
    analysis       TEXT,
    result         TEXT,
    output_file    TEXT,
    cost_usd       REAL NOT NULL DEFAULT 0,
    cost_estimated INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER,
    models         TEXT
);
CREATE INDEX IF NOT EXISTS runs_created_at ON runs (created_at DESC);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status);

CREATE TABLE IF NOT EXISTS spans (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    mode           TEXT NOT NULL,
    stage          TEXT NOT NULL,
    attempt        INTEGER NOT NULL DEFAULT 0,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    status         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    ended_at       TEXT NOT NULL,
    duration_ms    INTEGER NOT NULL,
    request        TEXT,
    response       TEXT,
    usage          TEXT,
    cost_usd       REAL,
    cost_estimated INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS spans_run ON spans (run_id, seq);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class SQLiteStore:
    kind = "sqlite"

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.files_dir = self.data_dir / "runs"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "traces.sqlite3"
        self._write_lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── runs ──────────────────────────────────────────────────────────────────

    def create_run(self, run: dict[str, Any]) -> None:
        row = _encode(run, RUN_JSON, RUN_BOOL)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._write_lock, self._conn() as conn:
            conn.execute(f"INSERT INTO runs ({cols}) VALUES ({marks})", list(row.values()))

    def update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        row = _encode({**fields, "updated_at": utcnow()}, RUN_JSON, RUN_BOOL)
        sets = ", ".join(f"{k} = ?" for k in row)
        with self._write_lock, self._conn() as conn:
            conn.execute(f"UPDATE runs SET {sets} WHERE id = ?", [*row.values(), run_id])

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _decode(row, RUN_JSON, RUN_BOOL) if row else None

    def list_runs(self, *, limit: int = 50, offset: int = 0, status: str | None = None, mode: str | None = None):
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if mode:
            where.append("mode = ?")
            args.append(mode)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM runs {clause}", args).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM runs {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", [*args, limit, offset]
            ).fetchall()
        return [_decode(r, RUN_JSON, RUN_BOOL) for r in rows], total

    def delete_run(self, run_id: str) -> None:
        with self._write_lock, self._conn() as conn:
            conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        shutil.rmtree(self._run_dir(run_id), ignore_errors=True)

    def fail_unfinished(self, reason: str) -> int:
        now = utcnow()
        with self._write_lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE runs SET status = 'error', error = ?, finished_at = ?, updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (reason, now, now),
            )
            return cur.rowcount

    # ── spans ─────────────────────────────────────────────────────────────────

    def add_span(self, span: dict[str, Any]) -> None:
        row = _encode(span, SPAN_JSON, SPAN_BOOL)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._write_lock, self._conn() as conn:
            conn.execute(f"INSERT INTO spans ({cols}) VALUES ({marks})", list(row.values()))

    def list_spans(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM spans WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        return [_decode(r, SPAN_JSON, SPAN_BOOL) for r in rows]

    # ── files ─────────────────────────────────────────────────────────────────

    def _run_dir(self, run_id: str) -> Path:
        return self.files_dir / run_id

    def put_file(self, run_id: str, name: str, data: bytes, content_type: str) -> None:
        path = self._run_dir(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_file(self, run_id: str, name: str) -> bytes | None:
        path = self._run_dir(run_id) / name
        return path.read_bytes() if path.is_file() else None

    # ── settings ──────────────────────────────────────────────────────────────

    def get_setting(self, key: str) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_setting(self, key: str, value: Any) -> None:
        with self._write_lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value), utcnow()),
            )

    # ── reporting ─────────────────────────────────────────────────────────────

    def stats(self, since: str) -> dict[str, Any]:
        finished = ", ".join(f"'{s}'" for s in FINISHED_STATUSES)
        with self._conn() as conn:
            q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731
            totals = q(
                f"""SELECT COUNT(*) AS runs,
                       SUM(CASE WHEN status IN ({finished}) THEN 1 ELSE 0 END) AS finished,
                       SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
                       AVG(CASE WHEN status IN ({finished}) THEN duration_ms END) AS avg_duration_ms,
                       AVG(CASE WHEN attempts > 0 THEN attempts END) AS avg_attempts
                    FROM runs WHERE created_at >= ?""",
                since,
            )[0]
            by_status = q("SELECT status, COUNT(*) AS n FROM runs WHERE created_at >= ? GROUP BY status", since)
            by_mode = q(
                """SELECT mode, COUNT(*) AS runs, COALESCE(SUM(cost_usd), 0) AS cost_usd,
                          AVG(CASE WHEN status = 'delivered' THEN cost_usd END) AS avg_cost_delivered
                   FROM runs WHERE created_at >= ? GROUP BY mode""",
                since,
            )
            by_stage = q(
                """SELECT stage, mode, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd,
                          AVG(duration_ms) AS avg_duration_ms,
                          SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
                   FROM spans WHERE started_at >= ? GROUP BY stage, mode""",
                since,
            )
            by_model = q(
                """SELECT provider, model, mode, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd,
                          SUM(COALESCE(json_extract(usage, '$.input_tokens'), 0)
                              + COALESCE(json_extract(usage, '$.text_input_tokens'), 0)
                              + COALESCE(json_extract(usage, '$.image_input_tokens'), 0)) AS input_tokens,
                          SUM(COALESCE(json_extract(usage, '$.output_tokens'), 0)) AS output_tokens
                   FROM spans WHERE started_at >= ? GROUP BY provider, model, mode""",
                since,
            )
            daily = q(
                """SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS runs,
                          SUM(CASE WHEN mode = 'live' THEN cost_usd ELSE 0 END) AS live_usd,
                          SUM(CASE WHEN mode = 'mock' THEN cost_usd ELSE 0 END) AS mock_usd
                   FROM runs WHERE created_at >= ? GROUP BY day ORDER BY day""",
                since,
            )
        return _stats_payload(since, totals, by_status, by_mode, by_stage, by_model, daily)


def _stats_payload(since, totals, by_status, by_mode, by_stage, by_model, daily) -> dict[str, Any]:
    modes = {r["mode"]: r for r in by_mode}

    def mode_val(mode: str, key: str, default=0.0):
        row = modes.get(mode)
        return row[key] if row and row[key] is not None else default

    finished = totals["finished"] or 0
    return {
        "since": since,
        "runs": totals["runs"] or 0,
        "finished": finished,
        "delivery_rate": round((totals["delivered"] or 0) / finished, 4) if finished else None,
        "avg_duration_ms": round(totals["avg_duration_ms"]) if totals["avg_duration_ms"] is not None else None,
        "avg_attempts": round(totals["avg_attempts"], 2) if totals["avg_attempts"] is not None else None,
        "by_status": {r["status"]: r["n"] for r in by_status},
        "by_mode": {r["mode"]: r["runs"] for r in by_mode},
        "cost": {
            "live_usd": round(mode_val("live", "cost_usd"), 6),
            "mock_estimated_usd": round(mode_val("mock", "cost_usd"), 6),
            "avg_per_delivered_live": _round(mode_val("live", "avg_cost_delivered", None)),
            "avg_per_delivered_mock": _round(mode_val("mock", "avg_cost_delivered", None)),
        },
        "by_stage": [dict(r) | {"cost_usd": round(r["cost_usd"], 6)} for r in by_stage],
        "by_model": [dict(r) | {"cost_usd": round(r["cost_usd"], 6)} for r in by_model],
        "daily": [dict(r) | {"live_usd": round(r["live_usd"] or 0, 6), "mock_usd": round(r["mock_usd"] or 0, 6)} for r in daily],
    }


def _round(value):
    return round(value, 6) if value is not None else None


def _encode(record: dict[str, Any], json_cols: tuple[str, ...], bool_cols: tuple[str, ...]) -> dict[str, Any]:
    out = {}
    for key, value in record.items():
        if key in json_cols and value is not None:
            value = json.dumps(value, ensure_ascii=False)
        elif key in bool_cols and value is not None:
            value = int(bool(value))
        out[key] = value
    return out


def _decode(row: sqlite3.Row, json_cols: tuple[str, ...], bool_cols: tuple[str, ...]) -> dict[str, Any]:
    out = dict(row)
    for key in json_cols:
        if out.get(key) is not None:
            out[key] = json.loads(out[key])
    for key in bool_cols:
        if key in out and out[key] is not None:
            out[key] = bool(out[key])
    return out
