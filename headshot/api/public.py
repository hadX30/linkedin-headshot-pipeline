"""
Public endpoints.

/health, POST /v1/headshots and GET /v1/headshots/{id}/image are the original API,
with the original request handling, responses, status codes and messages.

/v1/config and /v1/runs are new and separate: the same pipeline, run in the
background and polled, with responses that leave out prompts, costs and the
sensitive analysis fields.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from ..pipeline import create_run, current_mode, execute_run
from .common import ALLOWED_EXTENSIONS, UNSUPPORTED_TYPE, check_run_id, read_upload, start_run, state

router = APIRouter()


# ── Original API ──────────────────────────────────────────────────────────────

@router.get("/health")
def health() -> dict:
    """Basic readiness check."""
    return {"status": "ok"}


@router.post("/v1/headshots", status_code=201)
def create_headshot(request: Request, image: UploadFile = File(...)) -> dict:
    """Run the entire pipeline for an uploaded image and return its result (synchronous)."""
    app = state(request)
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=UNSUPPORTED_TYPE)

    try:
        chunks = []
        while chunk := image.file.read(1024 * 1024):
            chunks.append(chunk)
    finally:
        image.file.close()

    mode = current_mode(app.settings, app.store)
    job_id = create_run(app.settings, app.store, b"".join(chunks), image.filename or f"input{suffix}",
                        mode=mode, source="api-v1", require_keys=False)
    result = execute_run(app.settings, app.store, job_id)

    generated = result.get("generated_image")
    image_url = None
    if generated and app.store.get_file(job_id, generated) is not None:
        image_url = f"/v1/headshots/{job_id}/image"

    # Return an application-usable URL instead of exposing a server filesystem path.
    result["generated_image"] = image_url
    response = {"job_id": job_id, **result}
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=response)
    return response


@router.get("/v1/headshots/{job_id}/image")
def get_headshot_image(job_id: str, request: Request) -> Response:
    """Retrieve the generated image for a pipeline run (latest attempt, including manual review)."""
    try:
        safe_job_id = check_run_id(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Headshot not found") from None
    app = state(request)
    run = app.store.get_run(safe_job_id)
    data = app.store.get_file(safe_job_id, run["output_file"]) if run and run.get("output_file") else None
    if data is None:
        raise HTTPException(status_code=404, detail="Generated headshot not found")
    return Response(data, media_type="image/png", headers={"Content-Disposition": 'attachment; filename="headshot.png"'})


# ── New: background runs for the website ──────────────────────────────────────

@router.get("/v1/config")
def config(request: Request) -> dict:
    app = state(request)
    s = app.settings
    return {
        "mode": current_mode(s, app.store),
        "max_upload_mb": s.max_upload_mb,
        "max_attempts": s.max_retries,
        "thresholds": dict(s.thresholds),
    }


def _rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    allowed, retry_after = state(request).limiter.allow(client)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many photos from this address. Try again later.",
                            headers={"Retry-After": str(retry_after)})


@router.post("/v1/runs", status_code=202)
async def create(request: Request, image: UploadFile = File(...), scenario: str | None = Form(None)) -> dict:
    app = state(request)
    filename = image.filename or ""
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=UNSUPPORTED_TYPE)
    _rate_limit(request)
    raw = await read_upload(image, app.settings.max_upload_mb)
    mode = current_mode(app.settings, app.store)
    # A scenario only steers mock runs; live runs ignore it.
    return start_run(app, raw, filename, mode=mode, source="web", scenario=scenario if mode == "mock" else None)


@router.get("/v1/runs/{run_id}")
def get(run_id: str, request: Request) -> dict:
    run = state(request).store.get_run(check_run_id(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return public_view(run)


@router.get("/v1/runs/{run_id}/image")
def image(run_id: str, request: Request) -> Response:
    app = state(request)
    run = app.store.get_run(check_run_id(run_id))
    # Only a delivered result is shown on the website.
    if run is None or run["status"] != "delivered" or not run.get("output_file"):
        raise HTTPException(status_code=404, detail="No delivered headshot for this run")
    data = app.store.get_file(run["id"], run["output_file"])
    if data is None:
        raise HTTPException(status_code=404, detail="No delivered headshot for this run")
    return Response(data, media_type="image/png", headers={
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": 'inline; filename="headshot.png"',
    })


# ── view model for the new endpoint ───────────────────────────────────────────

def public_view(run: dict[str, Any]) -> dict[str, Any]:
    status = run["status"]
    attempts = (run.get("result") or {}).get("attempts", [])
    return {
        "id": run["id"],
        "status": status,
        "stage": run.get("stage"),
        "mode": run["mode"],
        "attempts": run.get("attempts", 0),
        "created_at": run["created_at"],
        "finished_at": run.get("finished_at"),
        "reason": run.get("reason"),
        "analysis": _public_analysis(run.get("analysis")),
        "reviews": [_public_attempt(a) for a in attempts],
        "image_url": f"/v1/runs/{run['id']}/image" if status == "delivered" and run.get("output_file") else None,
        "error": "Processing failed. Please try again." if status == "error" else None,
    }


def _d(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _public_analysis(analysis: dict | None) -> dict | None:
    """Read the original analyzer JSON defensively; Arabic text is not part of it (see P7)."""
    if not isinstance(analysis, dict):
        return None
    attire, subject = _d(analysis.get("attire")), _d(analysis.get("subject"))
    quality, action = _d(analysis.get("photo_quality")), _d(analysis.get("pipeline_action"))
    headwear, clothing = _d(attire.get("headwear")), _d(attire.get("clothing"))
    facial_hair, glasses = _d(subject.get("facial_hair")), _d(subject.get("glasses"))
    return {
        "headwear": {"type": headwear.get("type"), "description_ar": ""},
        "clothing": {"type": clothing.get("type"), "is_professional": clothing.get("is_professional"), "description_ar": ""},
        "needs_wardrobe_change": attire.get("needs_wardrobe_change"),
        "facial_hair": {"present": bool(facial_hair.get("present")), "description_ar": ""},
        "glasses": {"present": bool(glasses.get("present")), "description_ar": ""},
        "photo_quality": {k: quality.get(k) for k in ("face_angle", "lighting_quality", "background_type", "has_beauty_filters")},
        "decision": {
            "proceed": bool(action.get("proceed", False)),
            "mode": action.get("mode"),
            "reject_reason": action.get("reject_reason") or "none",
        },
    }


def _public_attempt(attempt: dict) -> dict:
    if attempt.get("action") == "generation_failed":
        return {"attempt": attempt["attempt"], "action": "generation_failed"}
    return {
        "attempt": attempt["attempt"],
        "action": attempt.get("action"),
        "checks": {name: {k: c.get(k) for k in ("score", "threshold", "passed", "issues_ar")}
                   for name, c in attempt.get("checks", {}).items()},
        "cultural": {"passed": attempt.get("cultural", {}).get("passed", True), "issues_ar": []},
    }
