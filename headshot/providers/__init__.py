"""Build the provider pair (vision LLM + image editor) for one run."""

from __future__ import annotations

from dataclasses import dataclass

from ..mock_data import Scenario
from ..settings import Settings
from .base import CallResult, ImageEditor, ImageInput, Part, VisionLLM


@dataclass
class Providers:
    llm: VisionLLM
    images: ImageEditor


def build_providers(settings: Settings, *, llm_mode: str, image_mode: str,
                    scenario: Scenario | None = None, profile: str = "") -> Providers:
    """
    llm_mode / image_mode: "live" or "mock".
    live+live is production; mock+mock is the free test mode; live+mock is the
    original `pipeline.py --mock` (real Claude stages, stand-in image).
    """
    if llm_mode == "live":
        from .claude import ClaudeLLM

        llm: VisionLLM = ClaudeLLM(settings)
    else:
        from .mock import MockLLM

        if scenario is None:
            raise ValueError("Mock mode needs a scenario.")
        llm = MockLLM(settings, scenario=scenario, profile=profile)

    if image_mode == "live":
        from .openai_images import OpenAIImageEditor

        images: ImageEditor = OpenAIImageEditor(settings)
    else:
        from .mock import MockImageEditor

        images = MockImageEditor(settings, scenario=scenario)
    return Providers(llm=llm, images=images)


__all__ = ["CallResult", "ImageEditor", "ImageInput", "Part", "Providers", "VisionLLM", "build_providers"]
