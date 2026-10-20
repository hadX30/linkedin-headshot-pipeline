import copy

import pytest

from headshot import pricing
from headshot.imaging import describe, extension_of, is_image, media_type_for, render_mock_headshot
from headshot.settings import Settings
from headshot.stages import enforce_thresholds, parse_json_reply, strip_prompt_fences

from .conftest import make_photo

T = Settings().thresholds


# ── original constants ────────────────────────────────────────────────────────

def test_defaults_match_the_original_config():
    s = Settings()
    assert s.mode == "live"  # real API calls unless mock is chosen explicitly
    assert s.thresholds == {"face_match": 85, "attire_accuracy": 90, "background": 95, "realism": 80}
    assert s.max_retries == 3
    assert s.generation_params == {"size": "1024x1024", "quality": "medium", "output_format": "png",
                                   "input_fidelity": "high", "n": 1}
    assert not s.review_fail_safe and not s.image_send_input_fidelity  # proposals off


# ── the original decision rule ────────────────────────────────────────────────

def report(face=95, attire=95, background=97, realism=90, cultural=True, **overall):
    r = {name: {"score": v, "pass": True, "issues": []} for name, v in
         (("face_match", face), ("attire_accuracy", attire), ("background", background), ("realism", realism))}
    r["cultural_sensitivity"] = {"pass": cultural, "issues": []}
    if overall:
        r["overall"] = overall
    return r


def decided(r, **kw):
    r = copy.deepcopy(r)
    enforce_thresholds(r, T, **kw)
    return r["overall"]


def test_all_pass_delivers():
    assert decided(report()) == {"pass": True, "action": "deliver", "retry_guidance": None}


def test_threshold_is_inclusive():
    assert decided(report(85, 90, 95, 80))["action"] == "deliver"
    assert decided(report(84, 90, 95, 80))["action"] == "retry"


def test_one_or_two_failures_retry_with_default_guidance():
    assert decided(report(face=80)) == {"pass": False, "action": "retry",
                                        "retry_guidance": "Checks failed: face_match. Adjust generation parameters."}


def test_model_guidance_is_kept():
    assert decided(report(face=80, retry_guidance="Fix eyes"))["retry_guidance"] == "Fix eyes"


def test_three_failures_go_to_manual_review():
    o = decided(report(10, 10, 10))
    assert o["action"] == "manual_review"
    assert o["retry_guidance"] == "Multiple checks failed (face_match, attire_accuracy, background). Manual review needed."


def test_cultural_gate_overrides_perfect_scores():
    assert decided(report(cultural=False))["action"] == "manual_review"


def test_model_verdict_is_ignored():
    assert decided(report(face=10, action="deliver", **{"pass": True}))["action"] == "retry"


def test_missing_checks_count_as_passed_as_in_the_original():
    r = report()
    del r["realism"], r["cultural_sensitivity"]
    assert decided(r)["action"] == "deliver"


def test_proposal_p1_fail_safe_counts_missing_as_failed():
    r = report()
    del r["cultural_sensitivity"]
    assert decided(r, fail_safe=True)["action"] == "manual_review"
    r = report()
    del r["realism"]
    assert decided(r, fail_safe=True)["action"] == "retry"


# ── original reply parsing ────────────────────────────────────────────────────

def test_parse_fenced_and_plain_json():
    assert parse_json_reply('{"a": 1}', "analyzer") == {"a": 1}
    assert parse_json_reply('```json\n{"a": 1}\n```', "analyzer") == {"a": 1}


def test_parse_error_message_is_the_original():
    with pytest.raises(ValueError, match=r"^Failed to parse validator response as JSON: .*\nRaw response:\nnope$"):
        parse_json_reply("nope", "validator")


def test_prompt_fences():
    assert strip_prompt_fences("```\nA\nB\n```") == "A\nB"
    assert strip_prompt_fences("```text\nA") == "A"
    assert strip_prompt_fences("plain") == "plain"


# ── images: read-only helpers ─────────────────────────────────────────────────

def test_media_type_from_extension_like_the_original():
    assert media_type_for("x.JPG") == "image/jpeg"
    assert media_type_for("x.webp") == "image/webp"
    assert media_type_for("x.heic") == "image/jpeg"  # original fallback
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
