import pytest

import config
from headshot import pricing
from headshot.imaging import describe, extension_of, is_image, media_type_for, render_mock_headshot
from headshot.runner import pipeline_rules
from headshot.settings import Settings

from .conftest import make_photo


# ── settings and the pipeline's rules ─────────────────────────────────────────

def test_settings_defaults():
    s = Settings()
    assert s.mode == "live"  # real API calls unless mock is chosen explicitly
    assert s.claude_model == "claude-sonnet-5-5" and s.image_model == "gpt-image-2.5-sunburst"
    assert s.generation_params == {"size": "1024x1024", "quality": "medium", "output_format": "png",
                                   "input_fidelity": "high", "n": 1}


def test_apps_report_the_rules_config_py_defines():
    rules = pipeline_rules()
    assert rules["ideal"] == config.VALIDATION_THRESHOLDS
    assert rules["floors"] == config.HARD_FLOORS
    assert rules["weights"] == config.COMPOSITE_WEIGHTS
    assert rules["deliver_at"] == config.COMPOSITE_DELIVER_THRESHOLD
    assert rules["max_candidates"] == config.N_CANDIDATES


def test_config_matches_the_documented_design():
    # The values the 40dbe16 commit message and FINDINGS.md describe.
    assert config.VALIDATION_THRESHOLDS == {"face_match": 85, "attire_accuracy": 90, "background": 80, "realism": 80}
    assert config.HARD_FLOORS == {"face_match": 78, "attire_accuracy": 80, "background": 65}
    assert config.COMPOSITE_WEIGHTS == {"face_match": 0.35, "attire_accuracy": 0.30, "realism": 0.20, "background": 0.15}
    assert config.COMPOSITE_DELIVER_THRESHOLD == 82 == config.EARLY_EXIT_COMPOSITE
    assert config.N_CANDIDATES == 3 == config.MAX_RETRIES


# ── images: read-only helpers ─────────────────────────────────────────────────

def test_media_type_from_extension():
    assert media_type_for("x.JPG") == "image/jpeg"
    assert media_type_for("x.webp") == "image/webp"
    assert media_type_for("x.heic") == "image/jpeg"  # fallback, as in the stages
    assert extension_of("dir/Photo.PNG") == ".png" and extension_of("noext") == ""


def test_describe_does_not_modify_the_image():
    raw = make_photo()
    meta = describe(raw, "p.jpg")
    assert meta["bytes"] == len(raw) and meta["width"] == 900 and meta["format"] == "JPEG"
    assert is_image(raw) and not is_image(b"not an image")


def test_mock_headshot_is_a_png():
    assert render_mock_headshot(make_photo(), attempt=1, label="t")[:8] == b"\x89PNG\r\n\x1a\n"


# ── pricing ───────────────────────────────────────────────────────────────────

def test_claude_cost():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_write_tokens": 10_000, "cache_read_tokens": 500_000}
    assert pricing.cost_usd("claude-sonnet-5-5", usage) == pytest.approx(3.125)


def test_image_cost_and_dated_snapshot():
    usage = {"text_input_tokens": 2_000, "image_input_tokens": 1_000, "output_tokens": 1_056}
    expected = (2_000 * 5 + 1_000 * 8 + 1_056 * 30) / 1_000_000
    assert pricing.cost_usd("gpt-image-2.5-sunburst-2026-09-08", usage) == pytest.approx(expected)


def test_unknown_model_has_no_price():
    assert pricing.cost_usd("some-new-model", {"input_tokens": 10}) is None
