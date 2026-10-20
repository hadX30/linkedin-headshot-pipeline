"""
Live Stage 3: OpenAI image edit, with the same request the original ImageGenerator made
(model, image, prompt, size, quality, n) and the same error wording.
"""

from __future__ import annotations

import base64
from typing import Any

from openai import OpenAI

from ..settings import Settings
from .base import CallResult, ImageInput

# input_fidelity is documented for the gpt-image-1 family only.
_FIDELITY_PREFIXES = ("gpt-image-1",)


class OpenAIImageEditor:
    provider = "openai"

    def __init__(self, settings: Settings):
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.image_model
        self.params = settings.generation_params
        self.send_input_fidelity = settings.image_send_input_fidelity  # proposal P2, off by default

    def request_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "size": self.params["size"],
            "quality": self.params["quality"],
            "n": self.params.get("n", 1),
        }
        if self.send_input_fidelity and self.model.startswith(_FIDELITY_PREFIXES):
            params["input_fidelity"] = self.params.get("input_fidelity", "high")
        return params

    def edit(self, *, image: ImageInput, prompt: str, attempt: int) -> CallResult:
        params = self.request_params()
        try:
            response = self.client.images.edit(
                image=(image.filename or f"{image.ref}.png", image.data, image.media_type),
                prompt=prompt,
                **params,
            )
            image_bytes = base64.b64decode(response.data[0].b64_json)
        except Exception as e:  # same wrapping as the original ImageGenerator
            raise RuntimeError(f"Image generation failed: {e}") from e

        usage: dict[str, int] = {}
        if getattr(response, "usage", None):
            details = getattr(response.usage, "input_tokens_details", None)
            usage = {
                "text_input_tokens": (getattr(details, "text_tokens", 0) if details else 0) or 0,
                "image_input_tokens": (getattr(details, "image_tokens", 0) if details else 0) or 0,
                "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
            }
        return CallResult(
            provider=self.provider,
            model=self.model,
            image=image_bytes,
            usage=usage,
            request_id=getattr(response, "_request_id", None),
            raw={k: getattr(response, k, None) for k in ("created", "size", "quality", "output_format")},
        )
