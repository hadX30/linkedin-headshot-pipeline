"""
The four pipeline stages, ported line for line from the original stages/*.py.

Requests, JSON handling, error messages and the threshold rule are the original
ones; tests/test_regression_legacy.py runs the frozen original code side by side
with this module and requires identical results. The only additions are the
tracing spans around each call (observability, no effect on decisions).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .prompts import (
    ANALYZER_MAX_TOKENS,
    ANALYZER_SYSTEM_PROMPT,
    ANALYZER_USER_TEXT,
    GENERATOR_SYSTEM_PROMPT,
    PROMPT_MAX_TOKENS,
    PROMPT_RETRY_SUFFIX,
    PROMPT_USER_TEMPLATE,
    VALIDATOR_GENERATED_LABEL,
    VALIDATOR_MAX_TOKENS,
    VALIDATOR_ORIGINAL_LABEL,
    VALIDATOR_SYSTEM_PROMPT,
    VALIDATOR_USER_TEXT,
)
from .providers import ImageInput, Part, Providers
from .store import TraceStore
from .tracing import Tracer

SCORED_CHECKS = ["face_match", "attire_accuracy", "background", "realism"]


@dataclass
class RunContext:
    run_id: str
    store: TraceStore
    tracer: Tracer
    providers: Providers
    source: ImageInput
    thresholds: dict[str, int]
    review_fail_safe: bool = False  # proposal P1, off by default


# ── Reply parsing (original) ──────────────────────────────────────────────────

def strip_json_fences(raw_text: str) -> str:
    """Original analyzer/validator handling of a reply wrapped in markdown fences."""
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```") and not in_block:
                in_block = True
                continue
            elif line.strip() == "```" and in_block:
                break
            elif in_block:
                json_lines.append(line)
        raw_text = "\n".join(json_lines)
    return raw_text


def parse_json_reply(raw_text: str, who: str) -> dict:
    """`who` is "analyzer" or "validator", as in the original error messages."""
    raw_text = strip_json_fences(raw_text)
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse {who} response as JSON: {e}\nRaw response:\n{raw_text}")


def strip_prompt_fences(prompt_text: str) -> str:
    """Original prompt-generator handling of a reply wrapped in markdown fences."""
    if prompt_text.startswith("```"):
        lines = prompt_text.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        prompt_text = "\n".join(lines)
    return prompt_text


# ── The decision rule (original QualityValidator._enforce_thresholds) ────────

def enforce_thresholds(report: dict, thresholds: dict[str, int], *, fail_safe: bool = False) -> None:
    """
    Re-apply the configured thresholds to the reviewer's scores and recompute
    report["overall"]: deliver, retry or manual_review. Mutates `report`, as before.

    fail_safe=True is proposal P1 (off by default): a missing check, or a missing
    cultural_sensitivity result, counts as failed instead of passed.
    """
    for check_name, threshold in thresholds.items():
        if check_name in report and "score" in report[check_name]:
            report[check_name]["pass"] = report[check_name]["score"] >= threshold

    checks_failed = []
    for check_name in SCORED_CHECKS:
        if fail_safe:
            check = report.get(check_name)
            if not isinstance(check, dict) or "score" not in check or not check.get("pass", False):
                checks_failed.append(check_name)
        elif check_name in report and not report[check_name].get("pass", True):
            checks_failed.append(check_name)

    cultural_pass = report.get("cultural_sensitivity", {}).get("pass", not fail_safe)

    if not cultural_pass:
        report["overall"] = {
            "pass": False,
            "action": "manual_review",
            "retry_guidance": report.get("overall", {}).get(
                "retry_guidance",
                "Cultural sensitivity check failed. Route to manual studio processing.",
            ),
        }
    elif len(checks_failed) >= 3:
        report["overall"] = {
            "pass": False,
            "action": "manual_review",
            "retry_guidance": report.get("overall", {}).get(
                "retry_guidance",
                f"Multiple checks failed ({', '.join(checks_failed)}). Manual review needed.",
            ),
        }
    elif len(checks_failed) > 0:
        report["overall"] = {
            "pass": False,
            "action": "retry",
            "retry_guidance": report.get("overall", {}).get(
                "retry_guidance",
                f"Checks failed: {', '.join(checks_failed)}. Adjust generation parameters.",
            ),
        }
    else:
        report["overall"] = {
            "pass": True,
            "action": "deliver",
            "retry_guidance": None,
        }


# ── Stages ────────────────────────────────────────────────────────────────────

def _llm_request(ctx: RunContext, system: str, content: list[Part], max_tokens: int) -> dict[str, Any]:
    """What was sent, for the trace: full text, images by name."""
    llm = ctx.providers.llm
    effective = llm.max_tokens_for(max_tokens) if hasattr(llm, "max_tokens_for") else max_tokens
    return {
        "system": system,
        "content": [{"image": p.ref, "media_type": p.media_type} if isinstance(p, ImageInput) else {"text": p} for p in content],
        "max_tokens": effective,
    }


def analyze(ctx: RunContext) -> dict:
    """Stage 1 (original PhotoAnalyzer.analyze)."""
    llm = ctx.providers.llm
    content: list[Part] = [ctx.source, ANALYZER_USER_TEXT]
    request = _llm_request(ctx, ANALYZER_SYSTEM_PROMPT, content, ANALYZER_MAX_TOKENS)
    with ctx.tracer.span(stage="analyze", attempt=0, provider=llm.provider, model=llm.model, request=request) as span:
        span.result = llm.complete(system=ANALYZER_SYSTEM_PROMPT, content=content,
                                   max_tokens=ANALYZER_MAX_TOKENS, stage="analyze")
        raw_text = span.result.text.strip()
        span.response = {"text": raw_text}
        analysis = parse_json_reply(raw_text, "analyzer")
        span.response["parsed"] = analysis
    return analysis


def write_prompt(ctx: RunContext, analysis: dict, *, attempt: int, retry_guidance: str | None = None) -> str:
    """Stage 2 (original PromptGenerator.generate)."""
    llm = ctx.providers.llm
    user_message = PROMPT_USER_TEMPLATE.format(analysis_json=json.dumps(analysis, indent=2, ensure_ascii=False))
    if retry_guidance:
        user_message += PROMPT_RETRY_SUFFIX.format(retry_guidance=retry_guidance)
    content: list[Part] = [user_message]
    request = _llm_request(ctx, GENERATOR_SYSTEM_PROMPT, content, PROMPT_MAX_TOKENS)
    with ctx.tracer.span(stage="prompt", attempt=attempt, provider=llm.provider, model=llm.model, request=request) as span:
        span.result = llm.complete(system=GENERATOR_SYSTEM_PROMPT, content=content,
                                   max_tokens=PROMPT_MAX_TOKENS, stage="prompt", attempt=attempt)
        prompt_text = strip_prompt_fences(span.result.text.strip())
        span.response = {"text": prompt_text}
    return prompt_text


def generate(ctx: RunContext, prompt: str, *, attempt: int) -> ImageInput:
    """Stage 3 (original ImageGenerator.generate): the source photo + prompt to the image edit API."""
    editor = ctx.providers.images
    name = f"attempt-{attempt}.png"
    request = {"prompt": prompt, "image": ctx.source.ref, "params": editor.request_params()}
    with ctx.tracer.span(stage="generate", attempt=attempt, provider=editor.provider, model=editor.model, request=request) as span:
        span.result = editor.edit(image=ctx.source, prompt=prompt, attempt=attempt)
        ctx.store.put_file(ctx.run_id, name, span.result.image, "image/png")
        span.response = {"file": name, "bytes": len(span.result.image)}
    return ImageInput(ref=name.removesuffix(".png"), data=span.result.image, media_type="image/png", filename=name)


def validate(ctx: RunContext, generated: ImageInput, *, attempt: int) -> dict:
    """Stage 4 (original QualityValidator.validate), including the threshold rule."""
    llm = ctx.providers.llm
    content: list[Part] = [VALIDATOR_ORIGINAL_LABEL, ctx.source, VALIDATOR_GENERATED_LABEL, generated, VALIDATOR_USER_TEXT]
    request = _llm_request(ctx, VALIDATOR_SYSTEM_PROMPT, content, VALIDATOR_MAX_TOKENS)
    with ctx.tracer.span(stage="validate", attempt=attempt, provider=llm.provider, model=llm.model, request=request) as span:
        span.result = llm.complete(system=VALIDATOR_SYSTEM_PROMPT, content=content,
                                   max_tokens=VALIDATOR_MAX_TOKENS, stage="validate", attempt=attempt)
        raw_text = span.result.text.strip()
        span.response = {"text": raw_text}
        qa_report = parse_json_reply(raw_text, "validator")
        enforce_thresholds(qa_report, ctx.thresholds, fail_safe=ctx.review_fail_safe)
        span.response["parsed"] = qa_report
    return qa_report


# ── Read-only summary of one review, for the dashboard and the website ───────

def attempt_summary(report: dict, thresholds: dict[str, int], *, attempt: int, file: str) -> dict:
    """Never raises: it only describes what the decision rule already did."""
    checks = {}
    for name in SCORED_CHECKS:
        check = report.get(name) if isinstance(report.get(name), dict) else {}
        score = check.get("score")
        checks[name] = {
            "score": score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
            "threshold": thresholds.get(name),
            "passed": bool(check.get("pass", True)),
            "issues": [str(i) for i in check.get("issues", []) if i] if isinstance(check.get("issues"), list) else [],
            "issues_ar": [],
        }
    cultural = report.get("cultural_sensitivity") if isinstance(report.get("cultural_sensitivity"), dict) else {}
    overall = report.get("overall") if isinstance(report.get("overall"), dict) else {}
    return {
        "attempt": attempt,
        "file": file,
        "checks": checks,
        "cultural": {
            "passed": bool(cultural.get("pass", True)),
            "issues": [str(i) for i in cultural.get("issues", []) if i] if isinstance(cultural.get("issues"), list) else [],
            "issues_ar": [],
        },
        "action": overall.get("action"),
        "guidance": overall.get("retry_guidance"),
    }
