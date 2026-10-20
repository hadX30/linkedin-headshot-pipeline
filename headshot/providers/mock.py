"""
Mock providers for tests and demos: same interface as the live ones, zero API calls.

They return fixture replies in the original JSON shapes, draw a stand-in image
locally, and report the token usage the live call would roughly have had, so traces
show an *estimated* cost without spending anything.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

from ..imaging import image_size, render_mock_headshot
from ..mock_data import Scenario, analysis_reply, validation_reply
from ..prompts import V3_BASE_TEMPLATE
from ..settings import Settings
from .base import CallResult, ImageInput, Part

# Rough output-token counts for one 1024x1024 image, by quality (GPT Image docs).
_IMAGE_OUTPUT_TOKENS = {"low": 272, "medium": 1056, "high": 4160}
_RETRY_MARKER = "This is a RETRY."


def _text_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _image_tokens(data: bytes) -> int:
    try:
        w, h = image_size(data)
    except Exception:  # noqa: BLE001 - unreadable test bytes still get an estimate
        return 1000
    return min(math.ceil(w * h / 750), 6400)


class MockLLM:
    provider = "mock"

    def __init__(self, settings: Settings, *, scenario: Scenario, profile: str):
        self.model = settings.claude_model  # costed as the live model it stands in for
        self.scenario = scenario
        self.profile = profile
        self.latency = settings.mock_latency_ms / 1000

    def complete(self, *, system: str, content: list[Part], max_tokens: int, stage: str, attempt: int = 0) -> CallResult:
        if self.latency > 0:
            time.sleep(self.latency)
        texts = [p for p in content if isinstance(p, str)]
        if stage == "analyze":
            reply = analysis_reply(self.profile, self.scenario)
        elif stage == "validate":
            reply = validation_reply(self.scenario, attempt)
        elif stage == "prompt":
            reply = _mock_prompt(texts[0] if texts else "")
        else:
            raise ValueError(f"Mock has no reply for stage {stage!r}")
        usage = {
            "input_tokens": _text_tokens(system) + sum(_text_tokens(t) for t in texts)
            + sum(_image_tokens(p.data) for p in content if isinstance(p, ImageInput)),
            "output_tokens": _text_tokens(reply),
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
        }
        return CallResult(self.provider, self.model, text=reply, usage=usage, stop_reason="end_turn",
                          raw={"scenario": self.scenario.name, "profile": self.profile})


class MockImageEditor:
    provider = "mock"

    def __init__(self, settings: Settings, *, scenario: Scenario | None):
        self.model = settings.image_model
        self.params = settings.generation_params
        self.scenario = scenario
        self.latency = settings.mock_latency_ms / 1000 * 2  # image calls are the slow ones

    def request_params(self) -> dict[str, Any]:
        return {"model": self.model, "size": self.params["size"], "quality": self.params["quality"], "n": self.params.get("n", 1)}

    def edit(self, *, image: ImageInput, prompt: str, attempt: int) -> CallResult:
        if self.latency > 0:
            time.sleep(self.latency)
        if self.scenario and attempt in self.scenario.failing_generations:
            raise RuntimeError("Image generation failed: mock image service unavailable (scripted failure)")
        label = self.scenario.name if self.scenario else "mock image"
        png = render_mock_headshot(image.data, attempt=attempt, label=label)
        usage = {
            "text_input_tokens": _text_tokens(prompt),
            "image_input_tokens": 1000,
            "output_tokens": _IMAGE_OUTPUT_TOKENS.get(self.params["quality"], 1056),
        }
        return CallResult(self.provider, self.model, image=png, usage=usage, raw={"scenario": label})


def _mock_prompt(user_text: str) -> str:
    """A deterministic stand-in for the prompt writer: subject notes + the original template."""
    try:
        start, end = user_text.index("```json") + 7, user_text.index("```", user_text.index("```json") + 7)
        analysis = json.loads(user_text[start:end])
    except ValueError:
        analysis = {}
    attire = analysis.get("attire", {})
    subject = analysis.get("subject", {})
    lines = [
        "Subject specifics for this photo:",
        f"- Headwear: {attire.get('headwear', {}).get('description') or 'none'}",
        f"- Clothing: {attire.get('clothing', {}).get('description') or 'n/a'}",
        f"- Facial hair: {subject.get('facial_hair', {}).get('description') or 'none'}",
        f"- Glasses: {subject.get('glasses', {}).get('frame_description') or 'none'}",
    ]
    if _RETRY_MARKER in user_text:
        lines += ["", "Adjusted after reviewer feedback:", user_text.split("feedback:\n", 1)[-1].strip()]
    return "\n".join(lines) + "\n\n" + V3_BASE_TEMPLATE
