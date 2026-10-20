import io

import pytest
from PIL import Image

from headshot import pricing
from headshot.imaging import ImageTooSmall, InvalidImage, normalize_upload, render_mock_headshot
from headshot.schemas import CulturalCheck, ScoredCheck, ValidationReport
from headshot.settings import Thresholds
from headshot.stages import decide

from .conftest import make_photo


# ── pricing ───────────────────────────────────────────────────────────────────

def test_claude_cost_uses_all_token_kinds():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_write_tokens": 10_000, "cache_read_tokens": 500_000}
    # 2.00 + 1.00 + 0.025 + 0.10
    assert pricing.cost_usd("claude-sonnet-5-5", usage) == pytest.approx(3.125)


def test_image_cost_and_dated_snapshot():
    usage = {"text_input_tokens": 2_000, "image_input_tokens": 1_000, "output_tokens": 1_056}
    expected = (2_000 * 5 + 1_000 * 8 + 1_056 * 30) / 1_000_000
    assert pricing.cost_usd("gpt-image-2.5-sunburst", usage) == pytest.approx(expected)
    assert pricing.cost_usd("gpt-image-2.5-sunburst-2026-09-08", usage) == pytest.approx(expected)


def test_unknown_model_has_no_price():
    assert pricing.cost_usd("some-new-model", {"input_tokens": 10}) is None
    assert not pricing.is_priced("some-new-model")


# ── decision rules ────────────────────────────────────────────────────────────

def report(face=95, attire=95, background=97, realism=90, cultural=True, guidance=""):
    check = lambda s: ScoredCheck(score=s, issues=[], issues_ar=[])  # noqa: E731
    return ValidationReport(
        face_match=check(face), attire_accuracy=check(attire), background=check(background), realism=check(realism),
        cultural_sensitivity=CulturalCheck(passed=cultural, issues=[], issues_ar=[]), retry_guidance=guidance,
    )


T = Thresholds()


def test_all_pass_delivers():
    assert decide(report(), T, attempt=1, max_attempts=3).action == "deliver"


def test_exact_threshold_passes():
    assert decide(report(face=85, attire=90, background=95, realism=80), T, attempt=1, max_attempts=3).action == "deliver"


def test_one_failure_retries_until_last_attempt():
    assert decide(report(face=80), T, attempt=1, max_attempts=3).action == "retry"
    last = decide(report(face=80), T, attempt=3, max_attempts=3)
    assert (last.action, last.reason) == ("manual_review", "max_attempts")


def test_cultural_gate_overrides_perfect_scores():
    d = decide(report(cultural=False), T, attempt=1, max_attempts=3)
    assert (d.action, d.reason) == ("manual_review", "cultural")


def test_three_failures_go_to_manual_review():
    d = decide(report(face=10, attire=10, background=10), T, attempt=1, max_attempts=3)
    assert (d.action, d.reason) == ("manual_review", "multiple_checks")


# ── imaging ───────────────────────────────────────────────────────────────────

def test_normalize_converts_and_downscales():
    raw = make_photo(size=(3000, 4000), fmt="PNG")
    img = normalize_upload(raw, max_edge=2048, min_edge=400)
    assert max(img.width, img.height) == 2048
    assert img.source_format == "PNG"
    with Image.open(io.BytesIO(img.data)) as decoded:
        assert decoded.format == "JPEG"


def test_normalize_rejects_non_images_and_tiny_photos():
    with pytest.raises(InvalidImage):
        normalize_upload(b"not an image at all", max_edge=2048, min_edge=400)
    with pytest.raises(ImageTooSmall):
        normalize_upload(make_photo(size=(300, 300)), max_edge=2048, min_edge=400)


def test_mock_headshot_is_square_png():
    img = normalize_upload(make_photo(), max_edge=2048, min_edge=400)
    png = render_mock_headshot(img.data, attempt=2, label="deliver")
    with Image.open(io.BytesIO(png)) as out:
        assert out.format == "PNG"
        assert out.size == (1024, 1024)
