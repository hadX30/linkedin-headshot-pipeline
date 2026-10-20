"""Live providers with the SDK clients swapped for fakes: no network, no cost."""

import base64
import dataclasses
from types import SimpleNamespace as NS

import pytest

from headshot.mock_data import PROFILES, SCENARIOS, analysis_for
from headshot.providers.base import ImageInput, ProviderError
from headshot.providers.claude import ClaudeLLM
from headshot.providers.openai_images import OpenAIImageEditor
from headshot.schemas import Analysis


def claude_response(text="", parsed=None, stop="end_turn"):
    return NS(
        id="msg_1", model="claude-sonnet-5-5", stop_reason=stop, stop_details=NS(category="cyber") if stop == "refusal" else None,
        content=[NS(type="thinking", thinking=""), NS(type="text", text=text)], parsed_output=parsed,
        usage=NS(input_tokens=1200, output_tokens=300, cache_creation_input_tokens=0, cache_read_input_tokens=900),
        _request_id="req_1",
    )


@pytest.fixture
def live_settings(settings):
    return dataclasses.replace(settings, anthropic_api_key="test", openai_api_key="test")


def test_claude_structured_uses_parse_with_cached_system(live_settings):
    llm = ClaudeLLM(live_settings)
    analysis = analysis_for(sorted(PROFILES)[0], SCENARIOS["deliver"])
    seen = {}

    def parse(**kwargs):
        seen.update(kwargs)
        return claude_response(text=analysis.model_dump_json(), parsed=analysis)

    llm.client = NS(messages=NS(parse=parse))
    img = ImageInput("input", b"\xff\xd8jpeg", "image/jpeg")
    result = llm.structured(system="SYS", text="Analyse", images=[img], schema=Analysis, stage="analyze")

    assert seen["output_format"] is Analysis
    assert seen["model"] == "claude-sonnet-5-5"
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    block = seen["messages"][0]["content"][0]
    assert block["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(block["source"]["data"]) == img.data
    assert result.parsed is analysis
    assert result.usage == {"input_tokens": 1200, "output_tokens": 300, "cache_write_tokens": 0, "cache_read_tokens": 900}
    assert result.request_id == "req_1"


def test_claude_fallback_without_structured_outputs(live_settings):
    llm = ClaudeLLM(dataclasses.replace(live_settings, claude_structured_outputs=False))
    analysis = analysis_for("casual", SCENARIOS["deliver"])
    llm.client = NS(messages=NS(create=lambda **kw: claude_response(text="```json\n" + analysis.model_dump_json() + "\n```")))
    result = llm.structured(system="SYS", text="x", images=[], schema=Analysis, stage="analyze")
    assert result.parsed == analysis


def test_claude_refusal_and_truncation_raise(live_settings):
    llm = ClaudeLLM(live_settings)
    llm.client = NS(messages=NS(create=lambda **kw: claude_response(text="no", stop="refusal")))
    with pytest.raises(ProviderError, match="declined"):
        llm.write(system="S", text="T", stage="prompt")
    llm.client = NS(messages=NS(create=lambda **kw: claude_response(text="partial", stop="max_tokens")))
    with pytest.raises(ProviderError) as err:
        llm.write(system="S", text="T", stage="prompt")
    assert err.value.retryable


def test_claude_write_strips_fences_and_sends_effort(live_settings):
    llm = ClaudeLLM(dataclasses.replace(live_settings, claude_effort="medium"))
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return claude_response(text="```\nEdit the photo.\n```")

    llm.client = NS(messages=NS(create=create))
    assert llm.write(system="S", text="T", stage="prompt").text == "Edit the photo."
    assert seen["output_config"] == {"effort": "medium"}


def image_response():
    return NS(
        created=1, size="1024x1024", quality="medium", output_format="png", _request_id="req_img",
        data=[NS(b64_json=base64.b64encode(b"PNGDATA").decode())],
        usage=NS(output_tokens=1056, input_tokens_details=NS(text_tokens=900, image_tokens=1100)),
    )


def test_openai_edit_params_and_usage(live_settings):
    editor = OpenAIImageEditor(live_settings)
    seen = {}

    def edit(**kwargs):
        seen.update(kwargs)
        return image_response()

    editor.client = NS(images=NS(edit=edit))
    result = editor.edit(image=ImageInput("input", b"jpeg", "image/jpeg"), prompt="P", attempt=1)
    assert seen["model"] == "gpt-image-2.5-sunburst"
    assert seen["image"] == ("input.jpg", b"jpeg", "image/jpeg")
    assert seen["output_format"] == "png" and seen["quality"] == "medium" and seen["size"] == "1024x1024"
    assert "input_fidelity" not in seen  # not documented for 2.5 models
    assert result.image == b"PNGDATA"
    assert result.usage == {"text_input_tokens": 900, "image_input_tokens": 1100, "output_tokens": 1056}


def test_openai_sends_input_fidelity_for_gpt_image_1(live_settings):
    editor = OpenAIImageEditor(dataclasses.replace(live_settings, image_model="gpt-image-1"))
    assert editor.request_params()["input_fidelity"] == "high"


def test_live_providers_refuse_without_keys(settings):
    with pytest.raises(ProviderError):
        ClaudeLLM(settings)
    with pytest.raises(ProviderError):
        OpenAIImageEditor(settings)
