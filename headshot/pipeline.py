"""
The orchestrator: create a run from an upload, then execute it stage by stage.

    analyze -> prompt -> [generate -> validate -> decide] x up to MAX_ATTEMPTS

The run row is updated as each stage starts, so anyone polling the run sees
live progress, and every provider call is stored as a span by the tracer.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .imaging import NormalizedImage, normalize_upload
from .mock_data import pick_profile, pick_scenario
from .providers import ImageInput, ProviderError, build_providers
from .settings import Settings
from .stages import RunContext, analyze, attempt_summary, decide, generate, retry_guidance, validate, write_prompt
from .store import TraceStore, new_id, utcnow
from .tracing import Tracer

log = logging.getLogger("headshot.pipeline")

INPUT_FILE = "input.jpg"


class LiveUnavailable(RuntimeError):
    """Live mode was requested but the API keys are not configured."""


def current_mode(settings: Settings, store: TraceStore) -> str:
    """The admin's saved choice wins over the HEADSHOT_MODE default."""
    saved = store.get_setting("mode")
    return saved if saved in ("mock", "live") else settings.mode


def create_run(
    settings: Settings,
    store: TraceStore,
    raw: bytes,
    *,
    mode: str,
    source: str,
    scenario: str | None = None,
) -> tuple[str, NormalizedImage]:
    """Validate the upload, store it, and record a queued run. Raises InvalidImage / ImageTooSmall."""
    if mode == "live" and not settings.live_available:
        raise LiveUnavailable("Live mode needs ANTHROPIC_API_KEY and OPENAI_API_KEY.")

    image = normalize_upload(raw, max_edge=settings.max_image_edge, min_edge=settings.min_image_edge)
    scenario_name = None
    if mode == "mock":
        scenario_name = pick_scenario(scenario or settings.mock_scenario, image.sha256).name

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
        "input": image.meta(),
        "cost_usd": 0,
        "cost_estimated": mode == "mock",
        "models": {"llm": settings.claude_model, "image": settings.image_model},
    })
    store.put_file(run_id, INPUT_FILE, image.data, "image/jpeg")
    return run_id, image


def execute_run(settings: Settings, store: TraceStore, run_id: str) -> dict[str, Any]:
    """Run every stage for a queued run. Never raises; the outcome is written to the run."""
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)

    started = time.perf_counter()
    mode = run["mode"]
    tracer = Tracer(store, run_id, mode)
    attempts: list[dict[str, Any]] = []

    def update(**fields: Any) -> None:
        store.update_run(run_id, fields)

    def finish(status: str, *, reason: str | None = None, error: str | None = None, output_file: str | None = None) -> dict:
        fields = {
            "status": status,
            "stage": None,
            "reason": reason,
            "error": error,
            "output_file": output_file,
            "finished_at": utcnow(),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "result": {"attempts": attempts},
        }
        if tracer.unpriced_models:
            fields["result"]["unpriced_models"] = sorted(tracer.unpriced_models)
        update(**fields)
        return store.get_run(run_id) or {}

    try:
        data = store.get_file(run_id, INPUT_FILE)
        if data is None:
            return finish("error", error="Input photo is missing from storage.")
        source = ImageInput(ref="input", data=data, media_type="image/jpeg")

        scenario = profile = None
        if mode == "mock":
            sha = run["input"]["sha256"]
            scenario = pick_scenario(run["scenario"], sha)
            profile = pick_profile(sha)
        providers = build_providers(settings, mode, scenario=scenario, profile=profile or "")
        ctx = RunContext(run_id=run_id, store=store, tracer=tracer, providers=providers, source=source)

        # Stage 1
        update(status="running", stage="analyze")
        analysis = analyze(ctx)
        update(analysis=analysis.model_dump())
        action = analysis.pipeline_action
        if not action.proceed:
            return finish("rejected", reason=action.reject_reason if action.reject_reason != "none" else "not_usable")

        # Stage 2
        update(stage="prompt")
        prompt = write_prompt(ctx, analysis, attempt=1)

        last_file = None
        for attempt in range(1, settings.max_attempts + 1):
            # Stage 3
            update(stage="generate", attempts=attempt)
            try:
                generated = generate(ctx, prompt, attempt=attempt)
            except ProviderError as exc:
                attempts.append({"attempt": attempt, "action": "generation_failed", "error": str(exc)})
                update(result={"attempts": attempts})
                if exc.retryable and attempt < settings.max_attempts:
                    continue
                raise
            last_file = f"{generated.ref}.png"

            # Stage 4
            update(stage="validate")
            report = validate(ctx, generated, attempt=attempt)
            decision = decide(report, settings.thresholds, attempt=attempt, max_attempts=settings.max_attempts)
            attempts.append(attempt_summary(report, decision, settings.thresholds, attempt=attempt, file=last_file))
            update(result={"attempts": attempts})

            if decision.action == "deliver":
                return finish("delivered", reason="passed", output_file=last_file)
            if decision.action == "manual_review":
                return finish("manual_review", reason=decision.reason, output_file=last_file)

            # Retry: Stage 2 rewrites the prompt with the reviewer's feedback
            update(stage="prompt")
            prompt = write_prompt(ctx, analysis, attempt=attempt + 1, guidance=retry_guidance(report, decision))

        return finish("manual_review", reason="max_attempts", output_file=last_file)

    except ProviderError as exc:
        return finish("error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - the run must always end in a final state
        log.exception("Run %s failed unexpectedly", run_id)
        return finish("error", error=f"Unexpected error: {exc}")
