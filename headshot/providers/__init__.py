"""Build the provider pair (vision LLM + image editor) for one run."""

from __future__ import annotations

from dataclasses import dataclass

from ..mock_data import Scenario
from ..settings import Settings
from .base import CallResult, ImageEditor, ImageInput, ProviderError, VisionLLM


@dataclass
class Providers:
    llm: VisionLLM
    images: ImageEditor


def build_providers(settings: Settings, mode: str, *, scenario: Scenario | None = None, profile: str = "") -> Providers:
    if mode == "live":
        # Imported lazily so mock mode never needs network clients.
        from .claude import ClaudeLLM
        from .openai_images import OpenAIImageEditor

        return Providers(llm=ClaudeLLM(settings), images=OpenAIImageEditor(settings))

    if mode == "mock":
        from .mock import MockImageEditor, MockLLM

        if scenario is None:
            raise ValueError("Mock mode needs a scenario.")
        return Providers(
            llm=MockLLM(settings, scenario=scenario, profile=profile),
            images=MockImageEditor(settings, scenario=scenario),
        )

    raise ValueError(f"Unknown mode {mode!r}")


__all__ = ["CallResult", "ImageEditor", "ImageInput", "ProviderError", "Providers", "VisionLLM", "build_providers"]
