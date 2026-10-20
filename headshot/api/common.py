"""Shared API helpers: app state access, upload reading, run-id checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request, UploadFile

from ..imaging import is_image
from ..jobs import JobRunner
from ..pipeline import LiveUnavailable, create_run
from ..settings import Settings
from ..store import TraceStore
from .ratelimit import RateLimiter

# The original API accepted these extensions (checked on the file name).
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
UNSUPPORTED_TYPE = "Unsupported image type. Upload a JPEG, PNG, or WebP image."


@dataclass
class AppState:
    settings: Settings
    store: TraceStore
    jobs: JobRunner
    limiter: RateLimiter


def state(request: Request) -> AppState:
    return request.app.state.headshot


def check_run_id(run_id: str) -> str:
    try:
        return str(uuid.UUID(run_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Run not found") from None


async def read_upload(upload: UploadFile, max_mb: int) -> bytes:
    """New /v1/runs endpoint: bounded read."""
    limit = max_mb * 1024 * 1024
    chunks, size = [], 0
    while chunk := await upload.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=f"The photo is larger than {max_mb} MB.")
        chunks.append(chunk)
    await upload.close()
    if not size:
        raise HTTPException(status_code=400, detail="The upload is empty.")
    return b"".join(chunks)


def start_run(app: AppState, raw: bytes, filename: str, *, mode: str, source: str, scenario: str | None) -> dict:
    """New /v1/runs endpoints: validate, store, and queue for background execution."""
    if not is_image(raw):
        raise HTTPException(status_code=415, detail=UNSUPPORTED_TYPE)
    try:
        run_id = create_run(app.settings, app.store, raw, filename, mode=mode, source=source, scenario=scenario)
    except LiveUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:  # unknown scenario
        raise HTTPException(status_code=400, detail=str(exc)) from None
    app.jobs.submit(run_id)
    return {"run_id": run_id, "status": "queued", "mode": mode, "poll_url": f"/v1/runs/{run_id}"}
