"""
Mock providers: same interface as the live ones, zero API calls, zero cost.

They return fixture data, draw a stand-in image locally, and report the token
usage the live call would roughly have had, so traces show an *estimated*
cost for the live models without spending anything.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..imaging import image_size, render_mock_headshot
from ..mock_data import Scenario, analysis_for
from ..prompts import RETRY_MARKER, V3_BASE_TEMPLATE
from ..schemas import SCORED_CHECKS, Analysis, CulturalCheck, ScoredCheck, ValidationReport
from ..settings import Settings
from .base import CallResult, ImageInput, ProviderError, T

# Rough output-token counts for one 1024x1024 image, by quality (GPT Image docs).
_IMAGE_OUTPUT_TOKENS = {"low": 272, "medium": 1056, "high": 4160}


def _text_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _claude_image_tokens(data: bytes) -> int:
    w, h = image_size(data)
    return min(math.ceil(w * h / 750), 6400)


class MockLLM:
    provider = "mock"

    def __init__(self, settings: Settings, *, scenario: Scenario, profile: str):
        self.model = settings.claude_model  # costed as the live model it stands in for
        self.scenario = scenario
        self.profile = profile
        self.latency = settings.mock_latency_ms / 1000

    def structured(
        self, *, system: str, text: str, images: list[ImageInput], schema: type[T], stage: str, attempt: int = 0
    ) -> CallResult:
        self._wait()
        if schema is Analysis:
            parsed: Any = analysis_for(self.profile, self.scenario)
        elif schema is ValidationReport:
            parsed = self._report(attempt)
        else:
            raise ProviderError(f"Mock has no fixture for {schema.__name__}.")
        body = parsed.model_dump_json()
        usage = {
            "input_tokens": _text_tokens(system) + _text_tokens(text) + sum(_claude_image_tokens(i.data) for i in images),
            "output_tokens": _text_tokens(body),
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
        }
        return CallResult(self.provider, self.model, text=body, parsed=parsed, usage=usage, stop_reason="end_turn",
                          raw={"scenario": self.scenario.name, "profile": self.profile})

    def write(self, *, system: str, text: str, stage: str, attempt: int = 0) -> CallResult:
        self._wait()
        analysis = analysis_for(self.profile, self.scenario)
        guidance = text.split(RETRY_MARKER, 1)[1].strip() if RETRY_MARKER in text else ""
        prompt = _mock_prompt(analysis, guidance)
        usage = {
            "input_tokens": _text_tokens(system) + _text_tokens(text),
            "output_tokens": _text_tokens(prompt),
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
        }
        return CallResult(self.provider, self.model, text=prompt, usage=usage, stop_reason="end_turn",
                          raw={"scenario": self.scenario.name, "profile": self.profile})

    def _report(self, attempt: int) -> ValidationReport:
        script = self.scenario.script_for(max(attempt, 1))
        checks = {}
        for name, score in zip(SCORED_CHECKS, script.scores):
            en, ar = script.issues.get(name, ("", ""))
            checks[name] = ScoredCheck(score=score, issues=[en] if en else [], issues_ar=[ar] if ar else [])
        cultural = CulturalCheck(
            passed=script.cultural_pass,
            issues=[] if script.cultural_pass else ["Traditional attire was altered."],
            issues_ar=[] if script.cultural_pass else ["تغيّر اللباس التقليدي"],
        )
        return ValidationReport(**checks, cultural_sensitivity=cultural, retry_guidance=script.guidance)

    def _wait(self) -> None:
        if self.latency > 0:
            time.sleep(self.latency)


class MockImageEditor:
    provider = "mock"

    def __init__(self, settings: Settings, *, scenario: Scenario):
        self.model = settings.image_model
        self.size = settings.image_size
        self.quality = settings.image_quality
        self.scenario = scenario
        self.latency = settings.mock_latency_ms / 1000 * 2  # image calls are the slow ones

    def request_params(self) -> dict[str, Any]:
        return {"model": self.model, "size": self.size, "quality": self.quality, "output_format": "png", "n": 1}

    def edit(self, *, image: ImageInput, prompt: str, attempt: int) -> CallResult:
        if self.latency > 0:
            time.sleep(self.latency)
        if attempt in self.scenario.failing_generations:
            raise ProviderError("Mock image service is temporarily unavailable (scripted failure).", retryable=True)
        png = render_mock_headshot(image.data, attempt=attempt, label=self.scenario.name)
        usage = {
            "text_input_tokens": _text_tokens(prompt),
            "image_input_tokens": 1000,
            "output_tokens": _IMAGE_OUTPUT_TOKENS.get(self.quality, 1056),
        }
        return CallResult(self.provider, self.model, image=png, usage=usage, raw={"scenario": self.scenario.name})


def _mock_prompt(analysis: Analysis, guidance: str) -> str:
    a = analysis.attire
    s = analysis.subject
    lines = [
        "Subject specifics for this photo:",
        f"- Headwear: {a.headwear.description or 'none'}",
        f"- Clothing: {a.clothing.description} ({'keep as-is' if not a.needs_wardrobe_change else a.recommended_outfit})",
        f"- Facial hair: {s.facial_hair.description or 'none'}",
        f"- Glasses: {s.glasses.description or 'none'}",
        f"- Accessories: {', '.join(s.accessories) or 'none'}",
        f"- Edit mode: {analysis.pipeline_action.mode}",
    ]
    if guidance:
        lines += ["", "Reviewer feedback addressed in this version:", f"- {guidance}"]
    return "\n".join(lines) + "\n\n" + V3_BASE_TEMPLATE
