"""Shared API helpers: app state access, upload reading, run-id checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request, UploadFile

from ..imaging import ImageTooSmall, InvalidImage
from ..jobs import JobRunner
from ..pipeline import LiveUnavailable, create_run
from ..settings import Settings
from ..store import TraceStore
from .ratelimit import RateLimiter


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


def start_run(app: AppState, raw: bytes, *, mode: str, source: str, scenario: str | None) -> dict:
    try:
        run_id, _ = create_run(app.settings, app.store, raw, mode=mode, source=source, scenario=scenario)
    except InvalidImage as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from None
    except ImageTooSmall as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except LiveUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:  # unknown scenario
        raise HTTPException(status_code=400, detail=str(exc)) from None
    app.jobs.submit(run_id)
    return {"run_id": run_id, "status": "queued", "mode": mode, "poll_url": f"/v1/runs/{run_id}"}
