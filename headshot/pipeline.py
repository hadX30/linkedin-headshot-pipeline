"""
The orchestrator: create a run from an upload, then execute it.

execute_run() is the original run_pipeline() ported line for line: same stages,
same retry loop, same statuses, same error strings, same returned `result` dict
(status, analysis, prompt, generated_image, validation, retries, error).
tests/test_regression_legacy.py runs the frozen original side by side with it.

Added around it, without touching any decision:
- the run's progress is saved as each stage starts (for polling clients),
- every provider call is recorded by the tracer,
- `reason` records *why* a run ended the way it did (observability only).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .imaging import describe, extension_of, media_type_for
from .mock_data import pick_profile, pick_scenario
from .providers import ImageInput, build_providers
from .settings import Settings
from .stages import RunContext, analyze, attempt_summary, generate, validate, write_prompt
from .store import TraceStore, new_id, utcnow
from .tracing import Tracer

log = logging.getLogger("headshot.pipeline")


class LiveUnavailable(RuntimeError):
    """Live mode was requested but the API keys are not configured."""


def current_mode(settings: Settings, store: TraceStore) -> str:
    """The admin's saved choice wins over the HEADSHOT_MODE default (live)."""
    saved = store.get_setting("mode")
    return saved if saved in ("mock", "live") else settings.mode


def create_run(
    settings: Settings,
    store: TraceStore,
    raw: bytes,
    filename: str,
    *,
    mode: str,
    source: str,
    scenario: str | None = None,
    mock_images: bool = False,
    require_keys: bool = True,
) -> str:
    """
    Store the upload byte-for-byte and record a queued run.

    mode="live": real API calls. mode="mock": no API calls (tests/demos).
    mock_images=True with mode="live" is the original `pipeline.py --mock`:
    real Claude stages, a stand-in image instead of the image API.
    """
    if require_keys and mode == "live" and not (
        settings.anthropic_api_key and (mock_images or settings.openai_api_key)
    ):
        raise LiveUnavailable("Live mode needs ANTHROPIC_API_KEY and OPENAI_API_KEY.")

    meta = describe(raw, filename)
    scenario_name = pick_scenario(scenario or settings.mock_scenario, meta["sha256"]).name if mode == "mock" else None

    stored_as = "input" + (extension_of(filename) or ".jpg")
    run_id = new_id()
    now = utcnow()
    store.create_run({
        "id": run_id,
        "created_at": now,
        "updated_at": now,
        "mode": mode,
        "scenario": scenario_name,
        "source": source,
        "status": "queued",
        "attempts": 0,
        "input": {**meta, "stored_as": stored_as},
        "cost_usd": 0,
        "cost_estimated": mode == "mock",
        "models": {
            "llm": settings.claude_model,
            "image": settings.image_model,
            "llm_mode": mode,
            "image_mode": "mock" if (mode == "mock" or mock_images) else "live",
        },
    })
    store.put_file(run_id, stored_as, raw, media_type_for(stored_as))
    return run_id


class LegacyOutput:
    """The files the original pipeline wrote into its output directory (CLI)."""

    def __init__(self, out_dir: Path, image_name: str):
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.prefix = f"{ts}_{os.path.splitext(os.path.basename(image_name))[0]}"

    def _path(self, suffix: str) -> Path:
        return self.dir / f"{self.prefix}_{suffix}"

    def analysis(self, analysis: dict) -> None:
        self._path("analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False))

    def prompt(self, prompt: str) -> None:
        self._path("prompt.txt").write_text(prompt)

    def image(self, attempt: int, data: bytes) -> str:
        path = self._path(f"headshot_v{attempt}.png")
        path.write_bytes(data)
        return str(path)

    def validation(self, attempt: int, report: dict) -> None:
        self._path(f"validation_v{attempt}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    def retry_prompt(self, attempt: int, prompt: str) -> None:
        self._path(f"prompt_retry{attempt}.txt").write_text(prompt)


def execute_run(
    settings: Settings,
    store: TraceStore,
    run_id: str,
    *,
    stop_after_stage: int = 4,
    output: LegacyOutput | None = None,
) -> dict[str, Any]:
    """Run the pipeline for a queued run and return the original `result` dict."""
    try:
        return _execute(settings, store, run_id, stop_after_stage=stop_after_stage, output=output)
    except KeyError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Something outside the original try blocks failed (for example a reply that is
        # valid JSON but not an object). Record it instead of leaving the run "running".
        log.exception("Run %s failed unexpectedly", run_id)
        result = {"status": "error", "analysis": None, "prompt": None, "generated_image": None,
                  "validation": None, "retries": 0, "error": f"Unexpected error: {exc}"}
        try:
            store.update_run(run_id, {"status": "error", "stage": None, "error": result["error"], "finished_at": utcnow()})
        except Exception:  # noqa: BLE001
            log.exception("Could not record the failure of run %s", run_id)
        return result


def _execute(settings: Settings, store: TraceStore, run_id: str, *, stop_after_stage: int,
             output: LegacyOutput | None) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)

    started = time.perf_counter()
    tracer = Tracer(store, run_id, run["mode"], estimated=bool(run["cost_estimated"]))
    result: dict[str, Any] = {
        "status": None,
        "analysis": None,
        "prompt": None,
        "generated_image": None,
        "validation": None,
        "retries": 0,
        "error": None,
    }
    attempts: list[dict] = []
    files = {"last": None}

    def update(**fields: Any) -> None:
        try:
            store.update_run(run_id, fields)
        except Exception:  # progress updates must never change the run's outcome
            log.exception("Could not update run %s", run_id)

    def finish(reason: str | None = None) -> dict[str, Any]:
        update(
            status=result["status"],
            stage=None,
            reason=reason,
            error=result["error"],
            output_file=files["last"],
            finished_at=utcnow(),
            duration_ms=round((time.perf_counter() - started) * 1000),
            result={"pipeline": _jsonable(result), "attempts": attempts,
                    **({"unpriced_models": sorted(tracer.unpriced_models)} if tracer.unpriced_models else {})},
        )
        return result

    models = run.get("models") or {}
    stored_as = run["input"].get("stored_as", "input.jpg")
    data = store.get_file(run_id, stored_as)
    source = ImageInput(ref="input", data=data or b"", media_type=media_type_for(stored_as),
                        filename=run["input"].get("filename") or stored_as)
    thresholds = dict(settings.thresholds)

    # ── Stage 1: Photo Analyzer ───────────────────────────────────────────
    update(status="running", stage="analyze")
    try:
        if data is None:
            raise FileNotFoundError("input photo is missing from storage")
        sha = run["input"]["sha256"]
        scenario = pick_scenario(run["scenario"], sha) if run.get("scenario") else None
        providers = build_providers(
            settings,
            llm_mode=models.get("llm_mode", run["mode"]),
            image_mode=models.get("image_mode", run["mode"]),
            scenario=scenario,
            profile=pick_profile(sha),
        )
        ctx = RunContext(run_id=run_id, store=store, tracer=tracer, providers=providers, source=source,
                         thresholds=thresholds, review_fail_safe=settings.review_fail_safe)
        analysis = analyze(ctx)
        result["analysis"] = analysis
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Stage 1 failed: {e}"
        return finish()

    if output:
        output.analysis(analysis)
    update(analysis=analysis)

    # Check if the pipeline should reject
    if not analysis.get("pipeline_action", {}).get("proceed", False):
        reason = analysis.get("pipeline_action", {}).get("reject_reason", "unknown")
        result["status"] = "manual_review"
        result["error"] = f"Photo rejected by analyzer: {reason}"
        return finish(reason="analyzer_rejected")

    if stop_after_stage <= 1:
        result["status"] = "partial"
        return finish()

    # ── Stage 2: Prompt Generator ─────────────────────────────────────────
    update(stage="prompt")
    try:
        prompt = write_prompt(ctx, analysis, attempt=1)
        result["prompt"] = prompt
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Stage 2 failed: {e}"
        return finish()

    if output:
        output.prompt(prompt)

    if stop_after_stage <= 2:
        result["status"] = "partial"
        return finish()

    # ── Stage 3 + 4: Generate → Validate → Retry Loop ────────────────────
    current_prompt = prompt
    for attempt in range(1, settings.max_retries + 1):
        update(stage="generate", attempts=attempt)

        # ── Stage 3: Image Generation ─────────────────────────────────
        try:
            generated = generate(ctx, current_prompt, attempt=attempt)
            files["last"] = generated.filename
            result["generated_image"] = output.image(attempt, generated.data) if output else generated.filename
        except Exception as e:
            result["status"] = "error"
            result["error"] = f"Stage 3 failed (attempt {attempt}): {e}"
            attempts.append({"attempt": attempt, "action": "generation_failed", "error": str(e)})
            update(result={"pipeline": _jsonable(result), "attempts": attempts})
            if attempt < settings.max_retries:
                log.info("Stage 3 generation failed, retrying... (%s)", e)
                continue
            return finish()

        if stop_after_stage <= 3:
            result["status"] = "partial"
            return finish()

        # ── Stage 4: Quality Validation ───────────────────────────────
        update(stage="validate")
        try:
            validation = validate(ctx, generated, attempt=attempt)
            result["validation"] = validation
        except Exception as e:
            result["status"] = "error"
            result["error"] = f"Stage 4 failed: {e}"
            return finish()

        if output:
            output.validation(attempt, validation)

        overall = validation.get("overall", {})
        action = overall.get("action", "manual_review")
        attempts.append(attempt_summary(validation, thresholds, attempt=attempt, file=generated.filename))
        update(result={"pipeline": _jsonable(result), "attempts": attempts})

        # ── Decision ──────────────────────────────────────────────────
        if action == "deliver":
            result["status"] = "delivered"
            result["retries"] = attempt - 1
            return finish(reason="passed")

        elif action == "retry" and attempt < settings.max_retries:
            retry_guidance = overall.get("retry_guidance", "")
            result["retries"] = attempt
            update(stage="prompt")
            # Regenerate the prompt with retry feedback
            try:
                current_prompt = write_prompt(ctx, analysis, attempt=attempt + 1, retry_guidance=retry_guidance)
                if output:
                    output.retry_prompt(attempt, current_prompt)
            except Exception as e:
                log.warning("Retry prompt generation failed: %s", e)
                # Continue with the existing prompt

        else:
            # Either manual_review or retries exhausted
            result["status"] = "manual_review"
            result["retries"] = attempt
            return finish(reason=_manual_reason(validation, action))

    # Should not reach here, but just in case
    result["status"] = "manual_review"
    return finish(reason="max_attempts")


def _manual_reason(validation: dict, action: str) -> str:
    """Why a run went to manual review (recorded for observability; decides nothing)."""
    if action == "retry":
        return "max_attempts"
    cultural = validation.get("cultural_sensitivity")
    if isinstance(cultural, dict) and cultural.get("pass") is False:
        return "cultural"
    return "multiple_checks"


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
