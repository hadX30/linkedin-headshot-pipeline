"""
Public endpoints the website calls. They never expose prompts, costs, raw model
output or sensitive analysis fields (apparent gender, age, skin tone).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from ..pipeline import current_mode
from .common import check_run_id, read_upload, start_run, state

router = APIRouter()

def _rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    allowed, retry_after = state(request).limiter.allow(client)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many photos from this address. Try again later.",
                            headers={"Retry-After": str(retry_after)})


@router.get("/health")
def health(request: Request) -> dict:
    app = state(request)
    return {"status": "ok", "mode": current_mode(app.settings, app.store), "store": app.store.kind}


@router.get("/v1/config")
def config(request: Request) -> dict:
    app = state(request)
    s = app.settings
    return {
        "mode": current_mode(s, app.store),
        "max_upload_mb": s.max_upload_mb,
        "max_attempts": s.max_attempts,
        "thresholds": s.thresholds.as_dict(),
    }


@router.post("/v1/runs", status_code=202)
async def create(request: Request, image: UploadFile = File(...), scenario: str | None = Form(None)) -> dict:
    app = state(request)
    _rate_limit(request)
    raw = await read_upload(image, app.settings.max_upload_mb)
    mode = current_mode(app.settings, app.store)
    # A scenario only steers mock runs; live runs ignore it.
    return start_run(app, raw, mode=mode, source="web", scenario=scenario if mode == "mock" else None)


@router.get("/v1/runs/{run_id}")
def get(run_id: str, request: Request) -> dict:
    app = state(request)
    run = app.store.get_run(check_run_id(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return public_view(run)


@router.get("/v1/runs/{run_id}/image")
def image(run_id: str, request: Request) -> Response:
    app = state(request)
    run = app.store.get_run(check_run_id(run_id))
    # Only a delivered result is ever shown to the public; manual-review images stay internal.
    if run is None or run["status"] != "delivered" or not run.get("output_file"):
        raise HTTPException(status_code=404, detail="No delivered headshot for this run")
    data = app.store.get_file(run["id"], run["output_file"])
    if data is None:
        raise HTTPException(status_code=404, detail="No delivered headshot for this run")
    return Response(data, media_type="image/png", headers={
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": 'inline; filename="headshot.png"',
    })


# ── view model ────────────────────────────────────────────────────────────────

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


def _public_analysis(analysis: dict | None) -> dict | None:
    if not analysis:
        return None
    attire, subject, quality, action = (analysis["attire"], analysis["subject"],
                                        analysis["photo_quality"], analysis["pipeline_action"])
    return {
        "headwear": {"type": attire["headwear"]["type"], "description_ar": attire["headwear"]["description_ar"]},
        "clothing": {
            "type": attire["clothing"]["type"],
            "is_professional": attire["clothing"]["is_professional"],
            "description_ar": attire["clothing"]["description_ar"],
        },
        "needs_wardrobe_change": attire["needs_wardrobe_change"],
        "facial_hair": {"present": subject["facial_hair"]["present"], "description_ar": subject["facial_hair"]["description_ar"]},
        "glasses": {"present": subject["glasses"]["present"], "description_ar": subject["glasses"]["description_ar"]},
        "photo_quality": {k: quality[k] for k in ("face_angle", "lighting_quality", "background_type", "has_beauty_filters")},
        "decision": {"proceed": action["proceed"], "mode": action["mode"], "reject_reason": action["reject_reason"]},
    }


def _public_attempt(attempt: dict) -> dict:
    if attempt.get("action") == "generation_failed":
        return {"attempt": attempt["attempt"], "action": "generation_failed"}
    return {
        "attempt": attempt["attempt"],
        "action": attempt["action"],
        "reason": attempt["reason"],
        "checks": {
            name: {k: c[k] for k in ("score", "threshold", "passed", "issues_ar")}
            for name, c in attempt["checks"].items()
        },
        "cultural": {"passed": attempt["cultural"]["passed"], "issues_ar": attempt["cultural"]["issues_ar"]},
    }
