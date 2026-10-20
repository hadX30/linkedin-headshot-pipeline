"""
The trace store interface. Two implementations share it: SQLite (local, zero
setup) and Supabase (hosted Postgres + Storage).

Records are plain dicts so they serialise straight to JSON for the API.

runs   one row per pipeline run: mode, status, stage, input metadata, analysis,
       final result, total cost, duration.
spans  one row per provider call: stage, attempt, model, the full request
       (prompts and parameters; images by reference), the response, token
       usage, cost and timing.
files  the input photo and every generated attempt, stored per run.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

RUN_STATUSES = ("queued", "running", "delivered", "manual_review", "rejected", "error")
FINISHED_STATUSES = ("delivered", "manual_review", "rejected", "error")
STAGES = ("analyze", "prompt", "generate", "validate")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return str(uuid.uuid4())


class TraceStore(Protocol):
    kind: str

    # runs
    def create_run(self, run: dict[str, Any]) -> None: ...
    def update_run(self, run_id: str, fields: dict[str, Any]) -> None: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def list_runs(
        self, *, limit: int = 50, offset: int = 0, status: str | None = None, mode: str | None = None
    ) -> tuple[list[dict[str, Any]], int]: ...
    def delete_run(self, run_id: str) -> None: ...
    def fail_unfinished(self, reason: str) -> int: ...

    # spans
    def add_span(self, span: dict[str, Any]) -> None: ...
    def list_spans(self, run_id: str) -> list[dict[str, Any]]: ...

    # files
    def put_file(self, run_id: str, name: str, data: bytes, content_type: str) -> None: ...
    def get_file(self, run_id: str, name: str) -> bytes | None: ...

    # settings
    def get_setting(self, key: str) -> Any: ...
    def set_setting(self, key: str, value: Any) -> None: ...

    # reporting
    def stats(self, since: str) -> dict[str, Any]: ...
