"""Provider interfaces. Stages talk to these; live and mock implement them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    """A provider call failed. `retryable` tells the pipeline whether a retry can help."""

    def __init__(self, message: str, *, retryable: bool = False, details: dict | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.details = details or {}


@dataclass
class ImageInput:
    """An image passed to a model, plus the name it is recorded under in traces."""

    ref: str  # e.g. "input", "attempt-2"
    data: bytes
    media_type: str


@dataclass
class CallResult:
    """What every provider call returns, so the tracer can record it uniformly."""

    provider: str
    model: str
    text: str = ""
    parsed: Any = None
    image: bytes | None = None
    usage: dict[str, int] = field(default_factory=dict)
    request_id: str | None = None
    stop_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VisionLLM(Protocol):
    provider: str
    model: str

    def structured(
        self, *, system: str, text: str, images: list[ImageInput], schema: type[T], stage: str, attempt: int = 0
    ) -> CallResult: ...

    def write(self, *, system: str, text: str, stage: str, attempt: int = 0) -> CallResult: ...


class ImageEditor(Protocol):
    provider: str
    model: str

    def edit(self, *, image: ImageInput, prompt: str, attempt: int) -> CallResult: ...

    def request_params(self) -> dict[str, Any]: ...
