"""Provider interfaces. The stages talk to these; live and mock implement them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Union


@dataclass
class ImageInput:
    """An image passed to a model, sent byte-for-byte as stored (no re-encoding)."""

    ref: str          # name in traces, e.g. "input", "attempt-2"
    data: bytes
    media_type: str   # from the file extension, exactly as the original stages did
    filename: str = ""  # multipart filename for the image API


# A message part: plain text or an image.
Part = Union[str, ImageInput]


@dataclass
class CallResult:
    """What every provider call returns, so the tracer can record it uniformly."""

    provider: str
    model: str
    text: str = ""
    image: bytes | None = None
    usage: dict[str, int] = field(default_factory=dict)
    request_id: str | None = None
    stop_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class VisionLLM(Protocol):
    provider: str
    model: str

    def complete(self, *, system: str, content: list[Part], max_tokens: int, stage: str, attempt: int = 0) -> CallResult:
        """Send one user turn; `text` is the reply's text, unstripped."""


class ImageEditor(Protocol):
    provider: str
    model: str

    def edit(self, *, image: ImageInput, prompt: str, attempt: int) -> CallResult:
        """Edit `image` with `prompt`; `image` on the result is the generated PNG bytes."""

    def request_params(self) -> dict[str, Any]: ...
