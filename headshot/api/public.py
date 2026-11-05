"""
Public endpoints.

/health, POST /v1/headshots and GET /v1/headshots/{id}/image are the pipeline's
own API (the contract of the team's api.py): the request runs the whole pipeline
and returns its result; the image endpoint serves the selected candidate.

/v1/config and /v1/runs are for the website: the same pipeline, run in the
background and polled, with responses that leave out prompts, costs and the
sensitive analysis fields.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from ..runner import create_run, current_mode, execute_run, pipeline_rules
from .common import ALLOWED_EXTENSIONS, UNSUPPORTED_TYPE, check_run_id, read_upload, start_run, state

router = APIRouter()


# ── The pipeline's API ────────────────────────────────────────────────────────

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
    execute_run(app.settings, app.store, job_id)
    run = app.store.get_run(job_id) or {}
    result = dict((run.get("result") or {}).get("pipeline") or {})

    # Prefer the pipeline's selected winner over the raw last attempt, and return an
    # application-usable URL instead of a server file name.
    result["generated_image"] = f"/v1/headshots/{job_id}/image" if run.get("output_file") else None
    result.pop("final_image", None)
    response = {"job_id": job_id, **result}
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=response)
    return response


@router.get("/v1/headshots/{job_id}/image")
def get_headshot_image(job_id: str, request: Request) -> Response:
    """Retrieve the generated image for a pipeline run: the selected candidate."""
    try:
        safe_job_id = check_run_id(job_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Headshot not found") from None
    app = state(request)
    run = app.store.get_run(safe_job_id) or {}
    data = app.store.get_file(safe_job_id, run["output_file"]) if run.get("output_file") else None
    if data is None:
        raise HTTPException(status_code=404, detail="Generated headshot not found")
    return Response(data, media_type="image/png", headers={"Content-Disposition": 'attachment; filename="headshot.png"'})


# ── For the website: background runs ──────────────────────────────────────────

@router.get("/v1/config")
def config(request: Request) -> dict:
    app = state(request)
    rules = pipeline_rules()
    return {
        "mode": current_mode(app.settings, app.store),
        "max_upload_mb": app.settings.max_upload_mb,
        "max_attempts": rules["max_candidates"],
        "thresholds": rules["ideal"],
        "decision": {k: rules[k] for k in ("floors", "weights", "deliver_at", "max_candidates")},
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


# ── view model for the website ────────────────────────────────────────────────

def public_view(run: dict[str, Any]) -> dict[str, Any]:
    status = run["status"]
    result = run.get("result") or {}
    selected = (result.get("pipeline") or {}).get("selected_attempt")
    return {
        "id": run["id"],
        "status": status,
        "stage": run.get("stage"),
        "mode": run["mode"],
        "attempts": run.get("attempts", 0),
        "selected_attempt": selected,
        "created_at": run["created_at"],
        "finished_at": run.get("finished_at"),
        "reason": run.get("reason"),
        "analysis": _public_analysis(run.get("analysis")),
        "reviews": [_public_candidate(c) for c in result.get("candidates", [])],
        "image_url": f"/v1/runs/{run['id']}/image" if status == "delivered" and run.get("output_file") else None,
        "error": "Processing failed. Please try again." if status == "error" else None,
    }


def _d(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _public_analysis(analysis: dict | None) -> dict | None:
    """The analyzer's JSON, read defensively; Arabic text is not part of it."""
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


def _public_candidate(c: dict) -> dict:
    """One candidate as the website draws it: scores, floors, composite and the pipeline's verdict."""
    base = {"attempt": c["attempt"], "selected": bool(c.get("selected"))}
    if c.get("action") in ("generation_failed", "review_failed") or "checks" not in c:
        return {**base, "action": c.get("action") or "pending"}
    return {
        **base,
        "action": c.get("action"),
        "composite": c.get("composite"),
        "deliverable": c.get("action") == "deliver",
        "checks": {
            name: {
                "score": check.get("score"),
                "floor": check.get("floor"),
                "weight": check.get("weight"),
                "below_floor": bool(check.get("below_floor")),
                # the ideal threshold and whether it was met: reported, not gating
                "threshold": check.get("ideal"),
                "passed": bool(check.get("passed")),
                "issues_ar": [],
            }
            for name, check in (c.get("checks") or {}).items()
        },
        "cultural": {"passed": bool(c.get("cultural", {}).get("passed", True)), "issues_ar": []},
    }
