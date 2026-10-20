"""Live providers with the SDK clients swapped for fakes: request shapes, compatibility, errors."""

import base64
import dataclasses
from types import SimpleNamespace as NS

import pytest

from headshot.providers.base import ImageInput
from headshot.providers.claude import ClaudeLLM
from headshot.providers.openai_images import OpenAIImageEditor


@pytest.fixture
def live(settings):
    return dataclasses.replace(settings, anthropic_api_key="test", openai_api_key="test")


def reply(text, *, thinking=False):
    content = [NS(type="text", text=text)]
    if thinking:
        content.insert(0, NS(type="thinking", thinking="", signature="s"))
    return NS(id="m", model="claude-sonnet-5-5", stop_reason="end_turn", content=content, _request_id="req_1",
              usage=NS(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=7))


def test_claude_request_matches_the_original_shape(live):
    llm = ClaudeLLM(dataclasses.replace(live, claude_model="claude-sonnet-4-20250514"))
    seen = {}
    llm.client = NS(messages=NS(create=lambda **kw: seen.update(kw) or reply("{}")))
    img = ImageInput("input", b"\xff\xd8jpeg", "image/jpeg")
    result = llm.complete(system="SYS", content=[img, "Analyze"], max_tokens=2000, stage="analyze")
    assert seen["model"] == "claude-sonnet-4-20250514" and seen["max_tokens"] == 2000  # original limit kept
    assert seen["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    block = seen["messages"][0]["content"][0]
    assert block["source"]["media_type"] == "image/jpeg" and base64.b64decode(block["source"]["data"]) == img.data
    assert seen["messages"][0]["content"][1] == {"type": "text", "text": "Analyze"}
    assert "temperature" not in seen and "thinking" not in seen
    assert result.usage["cache_read_tokens"] == 7 and result.request_id == "req_1"


def test_single_text_part_is_sent_as_a_plain_string(live):
    llm = ClaudeLLM(live)
    seen = {}
    llm.client = NS(messages=NS(create=lambda **kw: seen.update(kw) or reply("ok")))
    llm.complete(system="S", content=["only text"], max_tokens=4000, stage="prompt")
    assert seen["messages"][0]["content"] == "only text"


def test_thinking_models_get_room_and_text_is_read_after_thinking(live):
    llm = ClaudeLLM(live)  # claude-sonnet-5-5 by default
    seen = {}
    llm.client = NS(messages=NS(create=lambda **kw: seen.update(kw) or reply("answer", thinking=True)))
    assert llm.complete(system="S", content=["x"], max_tokens=2000, stage="analyze").text == "answer"
    assert seen["max_tokens"] == 16000


def test_effort_is_sent_only_when_configured(live):
    llm = ClaudeLLM(dataclasses.replace(live, claude_effort="medium"))
    seen = {}
    llm.client = NS(messages=NS(create=lambda **kw: seen.update(kw) or reply("x")))
    llm.complete(system="S", content=["x"], max_tokens=2000, stage="analyze")
    assert seen["output_config"] == {"effort": "medium"}


def test_claude_errors_are_not_rewrapped(live):
    """The pipeline reports `Stage N failed: <the SDK's own message>`, as before."""
    llm = ClaudeLLM(live)

    def fail(**kw):
        raise RuntimeError("overloaded")

    llm.client = NS(messages=NS(create=fail))
    with pytest.raises(RuntimeError, match="^overloaded$"):
        llm.complete(system="S", content=["x"], max_tokens=10, stage="analyze")


def image_response():
    return NS(created=1, size="1024x1024", quality="medium", output_format="png", _request_id="req_img",
              data=[NS(b64_json=base64.b64encode(b"PNGDATA").decode())],
              usage=NS(output_tokens=1056, input_tokens_details=NS(text_tokens=900, image_tokens=1100)))


def test_image_request_matches_the_original_parameters(live):
    editor = OpenAIImageEditor(live)
    seen = {}
    editor.client = NS(images=NS(edit=lambda **kw: seen.update(kw) or image_response()))
    result = editor.edit(image=ImageInput("input", b"jpeg", "image/jpeg", "photo.jpg"), prompt="P", attempt=1)
    assert seen == {"image": ("photo.jpg", b"jpeg", "image/jpeg"), "prompt": "P", "model": "gpt-image-2.5-sunburst",
                    "size": "1024x1024", "quality": "medium", "n": 1}
    assert result.image == b"PNGDATA"
    assert result.usage == {"text_input_tokens": 900, "image_input_tokens": 1100, "output_tokens": 1056}


def test_image_errors_keep_the_original_wording(live):
    editor = OpenAIImageEditor(live)

    def fail(**kw):
        raise RuntimeError("content policy")

    editor.client = NS(images=NS(edit=fail))
    with pytest.raises(RuntimeError, match="^Image generation failed: content policy$"):
        editor.edit(image=ImageInput("input", b"x", "image/jpeg"), prompt="P", attempt=1)


def test_proposal_p2_input_fidelity_only_when_enabled_and_supported(live):
    assert "input_fidelity" not in OpenAIImageEditor(dataclasses.replace(live, image_model="gpt-image-1")).request_params()
    on = dataclasses.replace(live, image_send_input_fidelity=True)
    assert OpenAIImageEditor(dataclasses.replace(on, image_model="gpt-image-1")).request_params()["input_fidelity"] == "high"
    assert "input_fidelity" not in OpenAIImageEditor(on).request_params()  # not documented for 2.5 models
