"""Live Stage 1, 2 and 4 provider: Claude via the official Anthropic SDK."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from ..settings import Settings
from .base import CallResult, ImageInput, ProviderError, T

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
)

MAX_TOKENS = 16000


class ClaudeLLM:
    provider = "anthropic"

    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set; live mode is unavailable.")
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=180.0, max_retries=2)
        self.model = settings.claude_model
        self.effort = settings.claude_effort
        self.structured_outputs = settings.claude_structured_outputs

    # ── public ────────────────────────────────────────────────────────────────

    def structured(
        self, *, system: str, text: str, images: list[ImageInput], schema: type[T], stage: str, attempt: int = 0
    ) -> CallResult:
        content: list[dict[str, Any]] = [_image_block(img) for img in images]
        content.append({"type": "text", "text": text})

        if self.structured_outputs:
            response = self._call("parse", system, content, output_format=schema)
            parsed = response.parsed_output
            if parsed is None:
                raise ProviderError(f"{stage}: the model returned no structured output.", retryable=True)
        else:
            # For models without structured outputs: describe the schema in the prompt and validate.
            schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
            prompt = f"{system}\n\nReply with only one JSON object matching this JSON schema:\n{schema_text}"
            response = self._call("create", prompt, content)
            parsed = _parse_json(_text_of(response), schema, stage)

        return self._result(response, text=_text_of(response), parsed=parsed)

    def write(self, *, system: str, text: str, stage: str, attempt: int = 0) -> CallResult:
        response = self._call("create", system, [{"type": "text", "text": text}])
        body = _strip_fences(_text_of(response))
        if not body:
            raise ProviderError(f"{stage}: the model returned an empty prompt.", retryable=True)
        return self._result(response, text=body)

    # ── internals ─────────────────────────────────────────────────────────────

    def _call(self, method: str, system: str, content: list[dict[str, Any]], **extra: Any):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            # The system prompt is identical on every run, so cache it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
            **extra,
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}

        try:
            response = getattr(self.client.messages, method)(**kwargs)
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"Claude API error {exc.status_code}: {exc.message}",
                retryable=isinstance(exc, _RETRYABLE),
                details={"status": exc.status_code, "request_id": getattr(exc, "request_id", None)},
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Could not reach the Claude API: {exc}", retryable=True) from exc

        if response.stop_reason == "refusal":
            details = response.stop_details
            category = getattr(details, "category", None) if details else None
            raise ProviderError(f"Claude declined the request (category: {category}).", details={"category": category})
        if response.stop_reason == "max_tokens":
            raise ProviderError("Claude hit max_tokens before finishing.", retryable=True)
        return response

    def _result(self, response, *, text: str, parsed: BaseModel | None = None) -> CallResult:
        u = response.usage
        usage = {
            "input_tokens": u.input_tokens or 0,
            "output_tokens": u.output_tokens or 0,
            "cache_write_tokens": u.cache_creation_input_tokens or 0,
            "cache_read_tokens": u.cache_read_input_tokens or 0,
        }
        return CallResult(
            provider=self.provider,
            model=response.model or self.model,
            text=text,
            parsed=parsed,
            usage=usage,
            request_id=getattr(response, "_request_id", None),
            stop_reason=response.stop_reason,
            raw={
                "id": response.id,
                "stop_reason": response.stop_reason,
                "content_types": [block.type for block in response.content],
            },
        )


def _image_block(img: ImageInput) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": img.media_type,
            "data": base64.standard_b64encode(img.data).decode("ascii"),
        },
    }


def _text_of(response) -> str:
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _strip_fences(text: str) -> str:
    match = re.match(r"^```[a-zA-Z]*\n(.*?)\n```\s*$", text, re.S)
    return match.group(1).strip() if match else text


def _parse_json(text: str, schema: type[T], stage: str) -> T:
    body = _strip_fences(text)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError(f"{stage}: the reply had no JSON object.", retryable=True)
    try:
        return schema.model_validate_json(body[start : end + 1])
    except ValidationError as exc:
        raise ProviderError(f"{stage}: the reply did not match the schema: {exc.errors()[:3]}", retryable=True) from exc
