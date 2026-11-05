"""
Traced SDK clients. The pipeline's stages call `client.messages.create(...)` (Claude)
and `client.images.edit(...)` (GPT Image). These wrappers pass every call through
unchanged and record it as one span: the request (prompts and parameters, images
by name), the reply, token usage, cost and timing. Errors are recorded and
re-raised, so the stages see exactly what the provider returned.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from typing import Any, Protocol

from stages._response import extract_text  # the pipeline's own reader for the reply text

from .tracing import Tracer


class CallContext(Protocol):
    tracer: Tracer
    refs: dict[str, str]  # sha256 of an image -> its name in the run ("input", "attempt-2")
    attempt: int          # the candidate the current call belongs to (0 = analysis)
    input_path: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Claude ────────────────────────────────────────────────────────────────────

class TracedAnthropic:
    def __init__(self, inner: Any, run: CallContext, stage: str, *, provider: str):
        self._inner = inner
        self.messages = _TracedMessages(inner.messages, run, stage, provider)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _TracedMessages:
    def __init__(self, inner: Any, run: CallContext, stage: str, provider: str):
        self._inner, self._run, self._stage, self._provider = inner, run, stage, provider

    def create(self, **kwargs: Any) -> Any:
        model = kwargs.get("model") or ""
        request = claude_request(kwargs, self._run.refs)
        with self._run.tracer.span(stage=self._stage, attempt=self._run.attempt, provider=self._provider,
                                   model=model, request=request) as span:
            response = self._inner.create(**kwargs)
            span.model = getattr(response, "model", None) or model
            span.usage = claude_usage(response)
            span.response = claude_response(response)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def claude_request(kwargs: dict[str, Any], refs: dict[str, str]) -> dict[str, Any]:
    """What was sent, with images replaced by their name in the run."""
    system = kwargs.get("system")
    if isinstance(system, list):
        system = "".join(block.get("text", "") for block in system if isinstance(block, dict))
    request: dict[str, Any] = {"model": kwargs.get("model"), "max_tokens": kwargs.get("max_tokens"), "system": system}
    messages = kwargs.get("messages") or []
    if len(messages) == 1 and messages[0].get("role") == "user":
        request["content"] = _content(messages[0].get("content"), refs)
    else:
        request["messages"] = [{"role": m.get("role"), "content": _content(m.get("content"), refs)} for m in messages]
    extra = {k: v for k, v in kwargs.items() if k not in ("model", "max_tokens", "system", "messages")}
    if extra:
        request["params"] = json.loads(json.dumps(extra, default=str))
    return request


def _content(content: Any, refs: dict[str, str]) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text":
            parts.append({"text": block.get("text", "")})
        elif kind == "image":
            source = block.get("source") or {}
            parts.append({"image": _image_ref(source.get("data"), refs), "media_type": source.get("media_type")})
        else:
            parts.append({"type": kind})
    return parts


def _image_ref(data: Any, refs: dict[str, str]) -> str:
    try:
        digest = sha256(base64.b64decode(data or ""))
    except (binascii.Error, TypeError, ValueError):
        return "image"
    return refs.get(digest, f"image {digest[:12]}")


def claude_response(response: Any) -> dict[str, Any]:
    content = getattr(response, "content", None) or []
    try:
        text = extract_text(response)
    except ValueError:
        text = None
    return {
        "text": text,
        "content_types": [getattr(block, "type", None) for block in content],
        "stop_reason": getattr(response, "stop_reason", None),
        "id": getattr(response, "id", None),
        "request_id": getattr(response, "_request_id", None),
    }


def claude_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if not usage:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }


# ── GPT Image ─────────────────────────────────────────────────────────────────

class TracedOpenAI:
    def __init__(self, inner: Any, run: CallContext, *, provider: str):
        self._inner = inner
        self.images = _TracedImages(inner.images, run, provider)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _TracedImages:
    def __init__(self, inner: Any, run: CallContext, provider: str):
        self._inner, self._run, self._provider = inner, run, provider

    def edit(self, **kwargs: Any) -> Any:
        model = kwargs.get("model") or ""
        request = {
            "image": self._file_ref(kwargs.get("image")),
            "prompt": kwargs.get("prompt"),
            "params": json.loads(json.dumps({k: v for k, v in kwargs.items() if k not in ("image", "prompt")}, default=str)),
        }
        with self._run.tracer.span(stage="generate", attempt=self._run.attempt, provider=self._provider,
                                   model=model, request=request) as span:
            response = self._inner.edit(**kwargs)
            span.usage = image_usage(response)
            span.response = {
                **{k: getattr(response, k, None) for k in ("created", "size", "quality", "output_format")},
                "request_id": getattr(response, "_request_id", None),
            }
        return response

    def _file_ref(self, image: Any) -> str:
        name = getattr(image, "name", None) or (image[0] if isinstance(image, tuple) else None)
        if not isinstance(name, str):
            return "image"
        if os.path.abspath(name) == os.path.abspath(self._run.input_path):
            return "input"
        return os.path.basename(name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def image_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if not usage:
        return {}
    details = getattr(usage, "input_tokens_details", None)
    return {
        "text_input_tokens": (getattr(details, "text_tokens", 0) if details else 0) or 0,
        "image_input_tokens": (getattr(details, "image_tokens", 0) if details else 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }
