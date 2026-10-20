"""
Scripted stand-ins for the Anthropic and OpenAI clients, shared by the original
(legacy) code and the new code in the regression tests. Each call takes the next
scripted reply and records the request it was given, normalised so the two
implementations can be compared exactly.
"""

from __future__ import annotations

import base64
import hashlib
import io
from types import SimpleNamespace as NS

from PIL import Image


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def png(seed: int) -> bytes:
    img = Image.new("RGB", (64, 64), ((seed * 53) % 256, 120, (seed * 97) % 256))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


class Script:
    claude: list[dict] = []
    images: list[dict] = []
    claude_calls: list[dict] = []
    image_calls: list[dict] = []

    @classmethod
    def load(cls, spec: dict) -> None:
        cls.claude = [dict(x) for x in spec.get("claude", [])]
        cls.images = [dict(x) for x in spec.get("images", [])]
        cls.claude_calls = []
        cls.image_calls = []


def _system_text(system) -> str:
    if isinstance(system, list):  # the new code sends the same text as a cached block
        return "".join(block["text"] for block in system)
    return system


class FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self.messages = self

    def create(self, **kwargs):
        Script.claude_calls.append({
            "model": kwargs["model"],
            "max_tokens": kwargs["max_tokens"],
            "system": _system_text(kwargs["system"]),
            "messages": kwargs["messages"],
            "extra": sorted(k for k in kwargs if k not in ("model", "max_tokens", "system", "messages")),
        })
        item = Script.claude.pop(0)
        if "raise" in item:
            raise RuntimeError(item["raise"])
        content = [NS(type="text", text=item["text"])]
        if item.get("thinking_first"):
            content.insert(0, NS(type="thinking", thinking="", signature="sig"))
        return NS(id="msg_test", model=kwargs["model"], stop_reason="end_turn", content=content,
                  usage=NS(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeOpenAI:
    def __init__(self, *args, **kwargs):
        self.images = self

    def edit(self, **kwargs):
        image = kwargs["image"]
        data = image[1] if isinstance(image, tuple) else image.read()
        Script.image_calls.append({**{k: v for k, v in kwargs.items() if k != "image"}, "image_sha": sha(data)})
        item = Script.images.pop(0)
        if "raise" in item:
            raise RuntimeError(item["raise"])
        if item.get("no_data"):
            return NS(data=[NS(b64_json=None)], usage=None)
        return NS(data=[NS(b64_json=base64.b64encode(png(item["png"])).decode())], usage=None,
                  created=0, size=kwargs.get("size"), quality=kwargs.get("quality"), output_format="png")
