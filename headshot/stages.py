"""
The four pipeline stages. Each one calls a provider inside a traced span and
returns plain results; the orchestrator in pipeline.py decides what happens next.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .prompts import ANALYZER_SYSTEM, PROMPT_WRITER_SYSTEM, RETRY_MARKER, VALIDATOR_SYSTEM
from .providers import ImageInput, Providers
from .schemas import SCORED_CHECKS, Analysis, ValidationReport
from .settings import Thresholds
from .store import TraceStore
from .tracing import Tracer


@dataclass
class RunContext:
    run_id: str
    store: TraceStore
    tracer: Tracer
    providers: Providers
    source: ImageInput


# ── Stage 1 ───────────────────────────────────────────────────────────────────

def analyze(ctx: RunContext) -> Analysis:
    llm = ctx.providers.llm
    text = "Analyse this portrait photo."
    request = {"system": ANALYZER_SYSTEM, "text": text, "images": [ctx.source.ref], "schema": "Analysis"}
    with ctx.tracer.span(stage="analyze", attempt=0, provider=llm.provider, model=llm.model, request=request) as span:
        span.result = llm.structured(system=ANALYZER_SYSTEM, text=text, images=[ctx.source], schema=Analysis, stage="analyze")
        span.response = {"parsed": span.result.parsed.model_dump()}
    return span.result.parsed


# ── Stage 2 ───────────────────────────────────────────────────────────────────

def write_prompt(ctx: RunContext, analysis: Analysis, *, attempt: int, guidance: str = "") -> str:
    llm = ctx.providers.llm
    text = f"Photo analysis:\n```json\n{json.dumps(analysis.model_dump(), indent=2, ensure_ascii=False)}\n```"
    if guidance:
        text += f"\n\n{RETRY_MARKER}\n{guidance}"
    request = {"system": PROMPT_WRITER_SYSTEM, "text": text}
    with ctx.tracer.span(stage="prompt", attempt=attempt, provider=llm.provider, model=llm.model, request=request) as span:
        span.result = llm.write(system=PROMPT_WRITER_SYSTEM, text=text, stage="prompt", attempt=attempt)
        span.response = {"text": span.result.text}
    return span.result.text


# ── Stage 3 ───────────────────────────────────────────────────────────────────

def generate(ctx: RunContext, prompt: str, *, attempt: int) -> ImageInput:
    editor = ctx.providers.images
    name = f"attempt-{attempt}.png"
    request = {"prompt": prompt, "images": [ctx.source.ref], "params": editor.request_params()}
    with ctx.tracer.span(stage="generate", attempt=attempt, provider=editor.provider, model=editor.model, request=request) as span:
        span.result = editor.edit(image=ctx.source, prompt=prompt, attempt=attempt)
        ctx.store.put_file(ctx.run_id, name, span.result.image, "image/png")
        span.response = {"file": name, "bytes": len(span.result.image)}
    return ImageInput(ref=name.removesuffix(".png"), data=span.result.image, media_type="image/png")


# ── Stage 4 ───────────────────────────────────────────────────────────────────

def validate(ctx: RunContext, generated: ImageInput, *, attempt: int) -> ValidationReport:
    llm = ctx.providers.llm
    text = "Image 1 is the ORIGINAL photo. Image 2 is the EDITED headshot. Return the quality report."
    images = [ctx.source, generated]
    request = {"system": VALIDATOR_SYSTEM, "text": text, "images": [i.ref for i in images], "schema": "ValidationReport"}
    with ctx.tracer.span(stage="validate", attempt=attempt, provider=llm.provider, model=llm.model, request=request) as span:
        span.result = llm.structured(system=VALIDATOR_SYSTEM, text=text, images=images, schema=ValidationReport,
                                     stage="validate", attempt=attempt)
        span.response = {"parsed": span.result.parsed.model_dump()}
    return span.result.parsed


# ── Decision ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Decision:
    action: str  # "deliver" | "retry" | "manual_review"
    reason: str  # "passed" | "checks_failed" | "cultural" | "multiple_checks" | "max_attempts"
    failed: tuple[str, ...]


def decide(report: ValidationReport, thresholds: Thresholds, *, attempt: int, max_attempts: int) -> Decision:
    """
    The code makes the call, not the model: scores are compared with our
    thresholds, and the cultural gate is a hard stop.
    """
    limits = thresholds.as_dict()
    failed = tuple(name for name in SCORED_CHECKS if getattr(report, name).score < limits[name])

    if not report.cultural_sensitivity.passed:
        return Decision("manual_review", "cultural", failed)
    if len(failed) >= 3:
        return Decision("manual_review", "multiple_checks", failed)
    if failed:
        if attempt >= max_attempts:
            return Decision("manual_review", "max_attempts", failed)
        return Decision("retry", "checks_failed", failed)
    return Decision("deliver", "passed", failed)


def attempt_summary(report: ValidationReport, decision: Decision, thresholds: Thresholds, *, attempt: int, file: str) -> dict:
    limits = thresholds.as_dict()
    checks = {}
    for name in SCORED_CHECKS:
        check = getattr(report, name)
        checks[name] = {"score": check.score, "threshold": limits[name], "passed": check.score >= limits[name],
                        "issues": check.issues, "issues_ar": check.issues_ar}
    cultural = report.cultural_sensitivity
    return {
        "attempt": attempt,
        "file": file,
        "checks": checks,
        "cultural": {"passed": cultural.passed, "issues": cultural.issues, "issues_ar": cultural.issues_ar},
        "action": decision.action,
        "reason": decision.reason,
        "guidance": report.retry_guidance,
    }


def retry_guidance(report: ValidationReport, decision: Decision) -> str:
    if report.retry_guidance.strip():
        return report.retry_guidance.strip()
    issues = [i for name in decision.failed for i in getattr(report, name).issues]
    return "Fix these review findings: " + "; ".join(issues or decision.failed)
