"""
Private admin API behind ADMIN_TOKEN: traces, costs, files, the mock/live
switch, and test runs. The dashboard page itself is static and holds no data.
"""

from __future__ import annotations

import hmac
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import pricing
from ..imaging import media_type_for
from ..mock_data import SCENARIOS
from ..pipeline import current_mode
from .common import check_run_id, read_upload, start_run, state

UI_DIR = Path(__file__).parent / "admin_ui"
FILE_NAME = re.compile(r"^(input\.(jpg|jpeg|png|webp|gif)|attempt-\d{1,2}\.png)$")


def require_admin(request: Request) -> None:
    token = state(request).settings.admin_token
    if not token:
        raise HTTPException(status_code=503, detail="Admin is disabled. Set ADMIN_TOKEN in the API's environment.")
    supplied = request.headers.get("authorization", "")
    if not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:].encode(), token.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token", headers={"WWW-Authenticate": "Bearer"})


page = APIRouter()
api = APIRouter(prefix="/admin/api", dependencies=[Depends(require_admin)])


@page.get("/admin", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})


# ── overview ──────────────────────────────────────────────────────────────────

@api.get("/overview")
def overview(request: Request, days: int = 30) -> dict:
    app = state(request)
    s = app.settings
    days = max(1, min(days, 365))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return {
        "mode": current_mode(s, app.store),
        "live_available": s.live_available,
        "store": app.store.kind,
        "models": {
            "llm": s.claude_model,
            "llm_priced": pricing.is_priced(s.claude_model),
            "image": s.image_model,
            "image_priced": pricing.is_priced(s.image_model),
            "image_size": s.generation_params["size"],
            "image_quality": s.generation_params["quality"],
        },
        "thresholds": dict(s.thresholds),
        "max_attempts": s.max_retries,
        "proposals": {"review_fail_safe": s.review_fail_safe, "image_send_input_fidelity": s.image_send_input_fidelity},
        "prices_checked_on": pricing.PRICES_CHECKED_ON,
        "days": days,
        "stats": app.store.stats(since),
    }


@api.get("/scenarios")
def scenarios() -> list[dict]:
    return [{"name": "auto", "description": "Pick from the photo: deliver, or retry then deliver."}] + [
        {"name": sc.name, "description": sc.description} for sc in SCENARIOS.values()
    ]


# ── settings (the mock/live switch) ───────────────────────────────────────────

class ModeUpdate(BaseModel):
    mode: str


@api.put("/settings")
def update_settings(body: ModeUpdate, request: Request) -> dict:
    app = state(request)
    if body.mode not in ("mock", "live"):
        raise HTTPException(status_code=422, detail="mode must be 'mock' or 'live'")
    if body.mode == "live" and not app.settings.live_available:
        raise HTTPException(status_code=409, detail="Live mode needs ANTHROPIC_API_KEY and OPENAI_API_KEY on the server.")
    app.store.set_setting("mode", body.mode)
    return {"mode": body.mode}


# ── runs ──────────────────────────────────────────────────────────────────────

@api.get("/runs")
def list_runs(request: Request, limit: int = 25, offset: int = 0, status: str | None = None, mode: str | None = None) -> dict:
    rows, total = state(request).store.list_runs(
        limit=max(1, min(limit, 200)), offset=max(0, offset), status=status or None, mode=mode or None
    )
    keep = ("id", "created_at", "finished_at", "mode", "scenario", "source", "status", "stage", "attempts",
            "reason", "cost_usd", "cost_estimated", "duration_ms", "models")
    return {"total": total, "runs": [{k: r.get(k) for k in keep} for r in rows]}


@api.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    store = state(request).store
    run = store.get_run(check_run_id(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run, "spans": store.list_spans(run["id"])}


@api.get("/runs/{run_id}/files/{name}")
def get_file(run_id: str, name: str, request: Request) -> Response:
    if not FILE_NAME.match(name):
        raise HTTPException(status_code=404, detail="File not found")
    data = state(request).store.get_file(check_run_id(run_id), name)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(data, media_type=media_type_for(name), headers={"Cache-Control": "private, no-store"})


@api.post("/runs", status_code=202)
async def create_test_run(
    request: Request,
    image: UploadFile = File(...),
    mode: str = Form("mock"),
    scenario: str | None = Form(None),
) -> dict:
    app = state(request)
    if mode not in ("mock", "live"):
        raise HTTPException(status_code=422, detail="mode must be 'mock' or 'live'")
    raw = await read_upload(image, app.settings.max_upload_mb)
    return start_run(app, raw, image.filename or "input.jpg", mode=mode, source="admin",
                     scenario=scenario if mode == "mock" else None)


@api.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request) -> Response:
    state(request).store.delete_run(check_run_id(run_id))
    return Response(status_code=204)
