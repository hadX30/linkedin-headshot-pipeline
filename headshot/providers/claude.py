"""
Live Claude calls (stages 1, 2 and 4), with the same requests the original stages made.

Model-compatibility only; no pipeline logic lives here:
- The reply text is read from the first `text` block. Newer models can return a
  `thinking` block first; for older models this is the same as `content[0].text`.
- Models that think by default count that thinking toward `max_tokens`, so they get
  more room. Models without default thinking keep the original per-stage limits.
- API errors are not wrapped, so the pipeline reports them exactly as before
  ("Stage N failed: <error>").
"""

from __future__ import annotations

import base64
from typing import Any

import anthropic

from ..settings import Settings
from .base import CallResult, ImageInput, Part

# Model ids that do not think unless asked to (the original model is one of them).
_NO_DEFAULT_THINKING = (
    "claude-3",
    "claude-sonnet-4-0", "claude-sonnet-4-2025", "claude-sonnet-4-5",
    "claude-opus-4-0", "claude-opus-4-2025", "claude-opus-4-1", "claude-opus-4-5",
    "claude-haiku-4-5",
)
THINKING_MODEL_MAX_TOKENS = 16000


class ClaudeLLM:
    provider = "anthropic"

    def __init__(self, settings: Settings):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.claude_model
        self.effort = settings.claude_effort

    def max_tokens_for(self, original: int) -> int:
        if self.model.startswith(_NO_DEFAULT_THINKING):
            return original
        return max(original, THINKING_MODEL_MAX_TOKENS)

    def request(self, *, system: str, content: list[Part], max_tokens: int) -> dict[str, Any]:
        """The exact keyword arguments sent to messages.create (also recorded in traces)."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens_for(max_tokens),
            # The system prompt is identical on every run; caching it changes cost, not output.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": _content(content)}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    def complete(self, *, system: str, content: list[Part], max_tokens: int, stage: str, attempt: int = 0) -> CallResult:
        response = self.client.messages.create(**self.request(system=system, content=content, max_tokens=max_tokens))
        text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), "")
        usage = getattr(response, "usage", None)
        return CallResult(
            provider=self.provider,
            model=getattr(response, "model", None) or self.model,
            text=text,
            usage={
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            } if usage else {},
            request_id=getattr(response, "_request_id", None),
            stop_reason=getattr(response, "stop_reason", None),
            raw={"id": getattr(response, "id", None),
                 "content_types": [getattr(b, "type", None) for b in response.content]},
        )


def _content(parts: list[Part]) -> Any:
    # A single text part is sent as a plain string, as the original prompt generator did.
    if len(parts) == 1 and isinstance(parts[0], str):
        return parts[0]
    blocks = []
    for part in parts:
        if isinstance(part, ImageInput):
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": part.media_type,
                    "data": base64.standard_b64encode(part.data).decode("utf-8"),
                },
            })
        else:
            blocks.append({"type": "text", "text": part})
    return blocks
