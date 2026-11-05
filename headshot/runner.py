"""
Runs the pipeline for the apps: the API, the admin dashboard and the CLI.

The pipeline itself (pipeline.run_pipeline, stages/, config.py) is used as it is.
This module plugs into it at the seam tests/test_pipeline_integration.py uses:

- The stage classes pipeline.py looks up (PhotoAnalyzer, PromptGenerator,
  ImageGenerator, MockImageGenerator, QualityValidator) are replaced by
  subclasses that report progress to the run record: the analysis, each
  generated image, each review. They return exactly what the originals return.
- The SDK client constructors the stages call (anthropic.Anthropic, OpenAI)
  return the real client wrapped for tracing (live), or a scripted stand-in
  (mock), also traced.

Outside a run (`python pipeline.py`, the team's tests) both behave exactly like
the originals.
"""

from __future__ import annotations

import contextvars
import inspect
import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
import openai

import config as pipeline_config
import pipeline as team_pipeline
from stages import analyzer as stage_analyzer
from stages import image_generator as stage_image
from stages import prompt_generator as stage_prompt
from stages import validator as stage_validator

from . import mock
from .clients import TracedAnthropic, TracedOpenAI, sha256
from .imaging import describe, extension_of, media_type_for
from .settings import Settings
from .store import TraceStore, new_id, utcnow
from .tracing import Tracer

log = logging.getLogger("headshot.runner")


class LiveUnavailable(RuntimeError):
    """Live mode was requested but the API keys are not configured."""


def current_mode(settings: Settings, store: TraceStore) -> str:
    """The admin's saved choice wins over the HEADSHOT_MODE default (live)."""
    saved = store.get_setting("mode")
    return saved if saved in ("mock", "live") else settings.mode


def pipeline_rules() -> dict[str, Any]:
    """The pipeline's decision rules, as config.py defines them (for the API and dashboard)."""
    c = pipeline_config
    return {
        "ideal": dict(c.VALIDATION_THRESHOLDS),
        "floors": dict(c.HARD_FLOORS),
        "weights": dict(c.COMPOSITE_WEIGHTS),
        "deliver_at": c.COMPOSITE_DELIVER_THRESHOLD,
        "early_exit_at": c.EARLY_EXIT_COMPOSITE,
        "max_candidates": c.N_CANDIDATES,
    }


def pipeline_models() -> dict[str, Any]:
    c = pipeline_config
    return {
        "llm": c.CLAUDE_MODEL,
        "image": c.OPENAI_IMAGE_MODEL,
        "image_size": c.GENERATION_PARAMS.get("size"),
        "image_quality": c.GENERATION_PARAMS.get("quality"),
    }


# ── creating a run ────────────────────────────────────────────────────────────

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

    mode="live": real API calls. mode="mock": no API calls (tests and demos).
    mock_images=True with mode="live" is `pipeline.py --mock`: real Claude stages,
    and the image stage copies the photo instead of calling the image API.
    """
    if require_keys and mode == "live" and not (
        settings.anthropic_api_key and (mock_images or settings.openai_api_key)
    ):
        raise LiveUnavailable("Live mode needs ANTHROPIC_API_KEY and OPENAI_API_KEY.")

    meta = describe(raw, filename)
    scenario_name = mock.pick_scenario(scenario or settings.mock_scenario, meta["sha256"]).name if mode == "mock" else None

    stored_as = "input" + (extension_of(filename) or ".jpg")
    run_id = new_id()
    now = utcnow()
    models = pipeline_models()
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
            "llm": models["llm"],
            "image": models["image"],
            "llm_mode": mode,
            "image_mode": "mock" if (mode == "mock" or mock_images) else "live",
        },
    })
    store.put_file(run_id, stored_as, raw, media_type_for(stored_as))
    return run_id


# ── executing a run ───────────────────────────────────────────────────────────

_ACTIVE: contextvars.ContextVar["_Run | None"] = contextvars.ContextVar("headshot_run", default=None)


@dataclass
class _Run:
    """One run in progress: where its calls are traced and its progress is saved."""

    run_id: str
    store: TraceStore
    tracer: Tracer
    mock: bool
    scenario: mock.Scenario | None
    profile: str
    latency: float
    input_path: str
    refs: dict[str, str] = field(default_factory=dict)       # image sha256 -> "input" / "attempt-N"
    files: dict[str, int] = field(default_factory=dict)      # generated image path -> candidate number
    candidates: list[dict[str, Any]] = field(default_factory=list)
    attempt: int = 0      # the candidate the current call belongs to (0 = analysis)
    generations: int = 0  # image generations started

    def update(self, **fields: Any) -> None:
        try:
            self.store.update_run(self.run_id, fields)
        except Exception:  # progress updates must never change the run's outcome
            log.exception("Could not update run %s", self.run_id)

    def stage(self, name: str, attempt: int) -> None:
        self.attempt = attempt
        self.update(status="running", stage=name, attempts=self.generations)

    def analysis(self, analysis: Any) -> None:
        self.update(analysis=_jsonable(analysis))

    def image(self, attempt: int, path: str) -> None:
        name = f"attempt-{attempt}.png"
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.store.put_file(self.run_id, name, data, _content_type(data))
            self.refs[sha256(data)] = f"attempt-{attempt}"
        except Exception:
            log.exception("Could not store %s for run %s", name, self.run_id)
            name = None
        self.files[path] = attempt
        self._candidate(attempt).update(file=name)
        self._save()

    def generation_failed(self, attempt: int, error: Exception) -> None:
        self._candidate(attempt).update(action="generation_failed", error=str(error))
        self._save()

    def review(self, attempt: int, report: dict[str, Any]) -> None:
        self._candidate(attempt).update(_review_summary(report))
        self._save()

    def review_failed(self, attempt: int, error: Exception) -> None:
        self._candidate(attempt).update(action="review_failed", error=str(error))
        self._save()

    def _candidate(self, attempt: int) -> dict[str, Any]:
        for c in self.candidates:
            if c["attempt"] == attempt:
                return c
        c = {"attempt": attempt}
        self.candidates.append(c)
        return c

    def _save(self) -> None:
        self.update(result={"candidates": self.candidates})

    def finish(self, result: dict[str, Any], duration_ms: int) -> None:
        selected = result.get("selected_attempt")
        stored = {c["attempt"]: c.get("file") for c in self.candidates if c.get("file")}
        for c in self.candidates:
            c["selected"] = c["attempt"] == selected
        if selected in stored:
            output_file = stored[selected]
        else:  # no selection (partial run, error): the latest image, if any
            output_file = stored[max(stored)] if stored else None
        self.update(
            status=result.get("status") or "error",
            stage=None,
            reason=_reason(result),
            error=result.get("error"),
            output_file=output_file,
            finished_at=utcnow(),
            duration_ms=duration_ms,
            attempts=self.generations,
            result={
                "pipeline": self.public_result(result),
                "candidates": self.candidates,
                **({"unpriced_models": sorted(self.tracer.unpriced_models)} if self.tracer.unpriced_models else {}),
            },
        )

    def public_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """The pipeline's result with local file paths replaced by the run's file names."""
        out = _jsonable(result)
        selected = result.get("selected_attempt")
        for key in ("generated_image", "final_image"):
            path = result.get(key)
            if not path:
                continue
            attempt = self.files.get(path, selected if key == "final_image" else None)
            out[key] = f"attempt-{attempt}.png" if attempt else os.path.basename(str(path))
        return out


def execute_run(
    settings: Settings,
    store: TraceStore,
    run_id: str,
    *,
    stop_after_stage: int = 4,
    output_dir: str | os.PathLike | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Run pipeline.run_pipeline() on a stored upload and record everything.
    Returns the pipeline's own result dict (with local file paths).

    output_dir: where the pipeline writes its output files (the CLI); a temporary
    folder otherwise, since the images are kept in the trace store.
    """
    install()
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    started = time.perf_counter()
    models = run.get("models") or {}
    stored_as = run["input"].get("stored_as", "input.jpg")
    scenario = None
    if run["mode"] == "mock":
        scenario = mock.pick_scenario(run.get("scenario"), run["input"]["sha256"])

    with tempfile.TemporaryDirectory(prefix="headshot-") as tmp:
        ctx = _Run(
            run_id=run_id,
            store=store,
            tracer=Tracer(store, run_id, run["mode"], estimated=bool(run["cost_estimated"])),
            mock=run["mode"] == "mock",
            scenario=scenario,
            profile=mock.pick_profile(run["input"]["sha256"]),
            latency=settings.mock_latency_ms / 1000,
            input_path=os.path.join(tmp, _input_name(run["input"].get("filename"), stored_as)),
        )
        token = _ACTIVE.set(ctx)
        try:
            data = store.get_file(run_id, stored_as)
            if data is None:
                raise FileNotFoundError("the input photo is missing from storage")
            with open(ctx.input_path, "wb") as f:
                f.write(data)
            ctx.refs[sha256(data)] = "input"
            result = team_pipeline.run_pipeline(
                image_path=ctx.input_path,
                mock_generation=run["mode"] == "live" and models.get("image_mode") == "mock",
                stop_after_stage=stop_after_stage,
                output_dir=str(output_dir) if output_dir else os.path.join(tmp, "out"),
                verbose=verbose,
            )
        except Exception as exc:  # the pipeline reports its own failures; this catches anything else
            log.exception("Run %s failed outside the pipeline's own error handling", run_id)
            result = {"status": "error", "error": f"Unexpected error: {exc}"}
        finally:
            _ACTIVE.reset(token)
        ctx.finish(result, round((time.perf_counter() - started) * 1000))
    return result


# ── the seam ──────────────────────────────────────────────────────────────────

class _PhotoAnalyzer(stage_analyzer.PhotoAnalyzer):
    def analyze(self, *args: Any, **kwargs: Any) -> dict:
        run = _ACTIVE.get()
        if run:
            run.stage("analyze", 0)
        analysis = super().analyze(*args, **kwargs)
        if run:
            run.analysis(analysis)
        return analysis


class _PromptGenerator(stage_prompt.PromptGenerator):
    def generate(self, *args: Any, **kwargs: Any) -> str:
        run = _ACTIVE.get()
        if run:
            run.stage("prompt", run.generations + 1)  # the prompt for the next candidate
        return super().generate(*args, **kwargs)


def _observed_generate(base: type) -> type:
    """A subclass of an image generator that stores each candidate image as it is made."""

    class Observed(base):  # type: ignore[valid-type, misc]
        def generate(self, *args: Any, **kwargs: Any) -> str:
            run = _ACTIVE.get()
            if run is None:
                return super().generate(*args, **kwargs)
            run.generations += 1
            attempt = run.generations
            run.stage("generate", attempt)
            try:
                path = super().generate(*args, **kwargs)
            except Exception as exc:
                run.generation_failed(attempt, exc)
                raise
            run.image(attempt, path)
            return path

    Observed.__name__ = Observed.__qualname__ = base.__name__
    return Observed


class _QualityValidator(stage_validator.QualityValidator):
    def validate(self, *args: Any, **kwargs: Any) -> dict:
        run = _ACTIVE.get()
        if run is None:
            return super().validate(*args, **kwargs)
        generated = inspect.signature(super().validate).bind(*args, **kwargs).arguments.get("generated_path")
        attempt = run.files.get(generated, run.generations)
        run.stage("validate", attempt)
        try:
            report = super().validate(*args, **kwargs)
        except Exception as exc:
            run.review_failed(attempt, exc)
            raise
        run.review(attempt, report)
        return report


class _AnthropicModule:
    """Stands in for the `anthropic` module inside one stage module."""

    def __init__(self, stage: str):
        self._stage = stage

    def Anthropic(self, *args: Any, **kwargs: Any) -> Any:  # noqa: N802 - same name as the SDK class
        run = _ACTIVE.get()
        if run is None:
            return anthropic.Anthropic(*args, **kwargs)
        if run.mock:
            return TracedAnthropic(mock.MockAnthropic(run, self._stage), run, self._stage, provider="mock")
        return TracedAnthropic(anthropic.Anthropic(*args, **kwargs), run, self._stage, provider="anthropic")

    def __getattr__(self, name: str) -> Any:
        return getattr(anthropic, name)


def _openai_client(*args: Any, **kwargs: Any) -> Any:
    run = _ACTIVE.get()
    if run is None:
        return openai.OpenAI(*args, **kwargs)
    if run.mock:
        return TracedOpenAI(mock.MockOpenAI(run), run, provider="mock")
    return TracedOpenAI(openai.OpenAI(*args, **kwargs), run, provider="openai")


_install_lock = threading.Lock()
_installed = False


def install() -> None:
    """Plug into the pipeline (once per process). Transparent outside a run."""
    global _installed
    with _install_lock:
        if _installed:
            return
        for module, stage in ((stage_analyzer, "analyze"), (stage_prompt, "prompt"), (stage_validator, "validate")):
            module.anthropic = _AnthropicModule(stage)
        stage_image.OpenAI = _openai_client
        team_pipeline.PhotoAnalyzer = _PhotoAnalyzer
        team_pipeline.PromptGenerator = _PromptGenerator
        team_pipeline.ImageGenerator = _observed_generate(stage_image.ImageGenerator)
        team_pipeline.MockImageGenerator = _observed_generate(stage_image.MockImageGenerator)
        team_pipeline.QualityValidator = _QualityValidator
        _installed = True


# ── helpers ───────────────────────────────────────────────────────────────────

def _review_summary(report: dict[str, Any]) -> dict[str, Any]:
    """What the pipeline decided about one candidate, read from its own review report."""
    rules = pipeline_rules()
    overall = report.get("overall") or {}
    floors_failed = [str(item) for item in overall.get("floors_failed") or []]
    below_floor = {item.split(" ", 1)[0] for item in floors_failed}  # "background (58 < 65)" -> background
    checks = {}
    for name in stage_validator.NUMERIC_CHECKS:
        check = report.get(name) or {}
        checks[name] = {
            "score": check.get("score"),
            "passed": check.get("pass"),  # against the ideal threshold; reported, not gating
            "ideal": rules["ideal"].get(name),
            "floor": rules["floors"].get(name),
            "weight": rules["weights"].get(name),
            "below_floor": name in below_floor,
            "issues": list(check.get("issues") or []),
        }
    cultural = report.get("cultural_sensitivity") or {}
    return {
        "action": overall.get("action"),
        "composite": overall.get("composite"),
        "floors_pass": overall.get("floors_pass"),
        "floors_failed": floors_failed,
        "checks": checks,
        "cultural": {"passed": cultural.get("pass", True), "issues": list(cultural.get("issues") or [])},
        "guidance": overall.get("retry_guidance"),
    }


def _reason(result: dict[str, Any]) -> str | None:
    """Why the run ended the way it did, read from the pipeline's result (observability only)."""
    status, error = result.get("status"), result.get("error") or ""
    if status == "delivered":
        return "deliverable"
    if status != "manual_review":
        return None
    if error.startswith("Photo rejected by analyzer"):
        return "analyzer_rejected"
    if error.startswith("No candidate completed"):
        return "no_candidates"
    validation = result.get("validation") or {}
    if (validation.get("cultural_sensitivity") or {}).get("pass", True) is False:
        return "cultural"
    if (validation.get("overall") or {}).get("floors_failed"):
        return "floors"
    return "below_bar"


def _input_name(filename: str | None, stored_as: str) -> str:
    """The photo's own name for the pipeline's output files, made safe for a path."""
    stem = re.sub(r"[^\w.-]+", "_", os.path.splitext(os.path.basename(filename or ""))[0]).strip("._")
    return (stem[:60] or "input") + os.path.splitext(stored_as)[1]


def _content_type(data: bytes) -> str:
    fmt = (describe(data, "image").get("format") or "").lower()
    return {"jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}.get(fmt, "image/png")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
