"""Live Stage 3 provider: OpenAI GPT Image edits."""

from __future__ import annotations

import base64
from typing import Any

import openai

from ..settings import Settings
from .base import CallResult, ImageInput, ProviderError

_RETRYABLE = (openai.RateLimitError, openai.InternalServerError)

# input_fidelity is documented for the gpt-image-1 family; newer models are not
# documented to take it, so it is only sent where it is known to be accepted.
_FIDELITY_PREFIXES = ("gpt-image-1",)


class OpenAIImageEditor:
    provider = "openai"

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not set; live mode is unavailable.")
        self.client = openai.OpenAI(api_key=settings.openai_api_key, timeout=300.0, max_retries=1)
        self.model = settings.image_model
        self.size = settings.image_size
        self.quality = settings.image_quality
        self.input_fidelity = settings.image_input_fidelity

    def request_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "size": self.size,
            "quality": self.quality,
            "output_format": "png",
            "n": 1,
        }
        if self.input_fidelity and self.model.startswith(_FIDELITY_PREFIXES):
            params["input_fidelity"] = self.input_fidelity
        return params

    def edit(self, *, image: ImageInput, prompt: str, attempt: int) -> CallResult:
        params = self.request_params()
        try:
            response = self.client.images.edit(
                image=(f"{image.ref}.jpg", image.data, image.media_type),
                prompt=prompt,
                **params,
            )
        except openai.APIStatusError as exc:
            raise ProviderError(
                f"OpenAI API error {exc.status_code}: {exc.message}",
                retryable=isinstance(exc, _RETRYABLE),
                details={"status": exc.status_code, "request_id": getattr(exc, "request_id", None)},
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(f"Could not reach the OpenAI API: {exc}", retryable=True) from exc

        if not response.data or not response.data[0].b64_json:
            raise ProviderError("OpenAI returned no image data.", retryable=True)

        usage: dict[str, int] = {}
        if response.usage:
            details = response.usage.input_tokens_details
            usage = {
                "text_input_tokens": (details.text_tokens if details else 0) or 0,
                "image_input_tokens": (details.image_tokens if details else 0) or 0,
                "output_tokens": response.usage.output_tokens or 0,
            }

        return CallResult(
            provider=self.provider,
            model=self.model,
            image=base64.b64decode(response.data[0].b64_json),
            usage=usage,
            request_id=getattr(response, "_request_id", None),
            raw={
                "created": response.created,
                "size": response.size,
                "quality": response.quality,
                "output_format": response.output_format,
            },
        )
