"""
Regression: the new pipeline must behave exactly like the original.

The original code (tests/legacy_reference/, frozen from main @ fb4d30f) and the new
code are fed the same scripted model replies. For every scenario they must produce
the same status, retries, error text, analysis, prompt, validation, final image,
and send byte-identical requests to Claude and to the image API.

Both sides use the original models (claude-sonnet-4-20250514, gpt-image-1) so the
requests are comparable; model choice is configuration, not pipeline logic.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from headshot.pipeline import LegacyOutput, create_run, execute_run
from headshot.prompts import ANALYZER_SYSTEM_PROMPT, GENERATOR_SYSTEM_PROMPT, V3_BASE_TEMPLATE, VALIDATOR_SYSTEM_PROMPT
from headshot.settings import Settings
from headshot.store.sqlite import SQLiteStore

from . import regression_fakes as F
from .conftest import make_photo

LEGACY = Path(__file__).parent / "legacy_reference"
ORIGINAL_MODELS = {"claude_model": "claude-sonnet-4-20250514", "image_model": "gpt-image-1"}

# ── scripted replies ──────────────────────────────────────────────────────────


def analysis(proceed=True, reject_reason=None, *, drop_action=False):
    data = {
        "subject": {"apparent_gender": "male", "apparent_age_range": "adult", "skin_tone": "medium",
                    "facial_hair": {"present": True, "type": "full_beard", "description": "short dark beard"},
                    "expression": "slight_smile", "glasses": {"present": False, "type": "prescription", "frame_description": ""},
                    "visible_accessories": []},
        "attire": {"headwear": {"type": "shemagh_with_iqal", "color": "red and white", "description": "houndstooth shemagh, black iqal"},
                   "clothing": {"type": "thobe", "is_professional": True, "color": "white", "description": "white thobe"},
                   "needs_wardrobe_change": False, "recommended_outfit": "Keep as-is"},
        "photo_quality": {"resolution_sufficient": True, "face_visible": True, "face_angle": "frontal",
                          "lighting_quality": "natural_good", "has_beauty_filters": False, "multiple_faces": not proceed,
                          "background_type": "indoor"},
        "pipeline_action": {"proceed": proceed, "mode": "full_edit" if proceed else "reject",
                            "reject_reason": reject_reason, "notes": "n"},
    }
    if drop_action:
        del data["pipeline_action"]
    return json.dumps(data)


def review(face=93, attire=95, background=97, realism=90, *, cultural=True, guidance=..., drop=(), overall=True, raw_face=None):
    report = {
        "face_match": {"score": raw_face if raw_face is not None else face, "pass": True, "issues": []},
        "attire_accuracy": {"score": attire, "pass": True, "issues": []},
        "background": {"score": background, "pass": True, "issues": []},
        "realism": {"score": realism, "pass": True, "issues": []},
        "cultural_sensitivity": {"pass": cultural, "issues": [] if cultural else ["hijab coverage changed"]},
    }
    if overall:
        report["overall"] = {"pass": True, "action": "deliver"}
        if guidance is not ...:
            report["overall"]["retry_guidance"] = guidance
    for key in drop:
        report.pop(key, None)
    return json.dumps(report)


def say(text, **kw):
    return {"text": text, **kw}


def boom(msg="boom"):
    return {"raise": msg}


def img(n):
    return {"png": n}


A, P1, P2, P3 = say(analysis()), say("PROMPT ONE"), say("PROMPT TWO"), say("PROMPT THREE")
PASS = say(review())

SCENARIOS = {
    "deliver_first_try": {"claude": [A, P1, PASS], "images": [img(1)]},
    "retry_then_deliver": {"claude": [A, P1, say(review(face=80, guidance="Make the eyes dark brown.")), P2, PASS],
                           "images": [img(1), img(2)]},
    "retry_default_guidance": {"claude": [A, P1, say(review(face=80, overall=False)), P2, PASS], "images": [img(1), img(2)]},
    "retry_guidance_null": {"claude": [A, P1, say(review(face=80, guidance=None)), P2, PASS], "images": [img(1), img(2)]},
    "cultural_fail": {"claude": [A, P1, say(review(cultural=False))], "images": [img(1)]},
    "cultural_fail_with_good_scores_and_guidance": {"claude": [A, P1, say(review(cultural=False, guidance="Fix hijab."))], "images": [img(1)]},
    "three_checks_fail": {"claude": [A, P1, say(review(face=10, attire=10, background=10))], "images": [img(1)]},
    "two_checks_fail_then_cultural": {"claude": [A, P1, say(review(face=10, realism=10)), P2, say(review(cultural=False))],
                                      "images": [img(1), img(2)]},
    "exhausted": {"claude": [A, P1, say(review(face=80)), P2, say(review(face=81)), P3, say(review(face=82))],
                  "images": [img(1), img(2), img(3)]},
    "exact_thresholds_pass": {"claude": [A, P1, say(review(85, 90, 95, 80))], "images": [img(1)]},
    "analyzer_rejects": {"claude": [say(analysis(False, "multiple_faces"))]},
    "analyzer_no_action": {"claude": [say(analysis(drop_action=True))]},
    "generation_fails_once": {"claude": [A, P1, PASS], "images": [boom("rate limited"), img(2)]},
    "generation_always_fails": {"claude": [A, P1], "images": [boom(), boom(), boom()]},
    "generation_returns_no_data": {"claude": [A, P1, PASS], "images": [{"no_data": True}, img(2)]},
    "stage1_api_error": {"claude": [boom("overloaded")]},
    "stage1_invalid_json": {"claude": [say("I cannot comply.")]},
    "stage2_error": {"claude": [A, boom("prompt boom")]},
    "stage4_error": {"claude": [A, P1, boom("review boom")], "images": [img(1)]},
    "stage4_invalid_json": {"claude": [A, P1, say("{not json")], "images": [img(1)]},
    "stage4_string_score": {"claude": [A, P1, say(review(raw_face="92"))], "images": [img(1)]},
    "review_missing_check_counts_as_pass": {"claude": [A, P1, say(review(drop=("realism",)))], "images": [img(1)]},
    "review_missing_cultural_counts_as_pass": {"claude": [A, P1, say(review(drop=("cultural_sensitivity",)))], "images": [img(1)]},
    "fenced_replies": {"claude": [say("```json\n" + analysis() + "\n```"), say("```\nFENCED PROMPT\n```"),
                                  say("```json\n" + review() + "\n```")], "images": [img(1)]},
    "retry_prompt_generation_fails": {"claude": [A, P1, say(review(face=80, guidance="g")), boom("p2 boom"), PASS],
                                      "images": [img(1), img(2)]},
}


@pytest.fixture(scope="session")
def photo_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("photo") / "photo.jpg"
    path.write_bytes(make_photo())
    return path


@pytest.fixture(scope="session")
def legacy_results(tmp_path_factory, photo_path) -> dict:
    """Run every scenario through the ORIGINAL code, in its own interpreter."""
    work = tmp_path_factory.mktemp("legacy")
    (work / "scenarios.json").write_text(json.dumps(SCENARIOS))
    env = {"ANTHROPIC_API_KEY": "test", "OPENAI_API_KEY": "test", "PATH": "/usr/bin:/bin", "HOME": str(work)}
    proc = subprocess.run(
        [sys.executable, "-I", str(LEGACY / "run_scenarios.py"), str(work / "scenarios.json"), str(photo_path), str(work / "out.json")],
        cwd=LEGACY, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads((work / "out.json").read_text())


def run_new(tmp_path: Path, photo_path: Path, spec: dict, monkeypatch, **overrides) -> dict:
    import anthropic

    import headshot.providers.openai_images as openai_images

    monkeypatch.setattr(anthropic, "Anthropic", F.FakeAnthropic)
    monkeypatch.setattr(openai_images, "OpenAI", F.FakeOpenAI)
    settings = dataclasses.replace(Settings(), anthropic_api_key="test", openai_api_key="test", data_dir=tmp_path / "data",
                                   mock_latency_ms=0, **{**ORIGINAL_MODELS, **overrides})
    store = SQLiteStore(settings.data_dir)
    F.Script.load(spec)
    run_id = create_run(settings, store, photo_path.read_bytes(), photo_path.name, mode="live", source="test", require_keys=False)
    result = execute_run(settings, store, run_id, output=LegacyOutput(tmp_path / "out", photo_path.name))
    generated = result.get("generated_image")
    result = json.loads(json.dumps(result, default=str))
    result["generated_image_sha"] = F.sha(Path(generated).read_bytes()) if generated and Path(generated).is_file() else None
    result["generated_image"] = bool(generated)
    return {"result": result, "claude_calls": F.Script.claude_calls, "image_calls": F.Script.image_calls,
            "unused": [len(F.Script.claude), len(F.Script.images)], "store": store, "run_id": run_id}


@pytest.mark.parametrize("name", SCENARIOS)
def test_new_pipeline_matches_original(name, legacy_results, photo_path, tmp_path, monkeypatch):
    old = legacy_results[name]
    new = run_new(tmp_path, photo_path, SCENARIOS[name], monkeypatch)

    for key in ("status", "retries", "error", "analysis", "prompt", "validation", "generated_image", "generated_image_sha"):
        assert new["result"][key] == old["result"][key], f"{name}: {key} differs"
    assert new["claude_calls"] == old["claude_calls"], f"{name}: Claude requests differ"
    assert new["image_calls"] == old["image_calls"], f"{name}: image API requests differ"
    assert new["unused"] == old["unused"], f"{name}: different number of model calls"

    # The trace store agrees with what the pipeline returned.
    run = new["store"].get_run(new["run_id"])
    assert run["status"] == old["result"]["status"]
    assert run["error"] == old["result"]["error"]


def test_scenarios_cover_every_outcome(legacy_results):
    statuses = {r["result"]["status"] for r in legacy_results.values()}
    assert statuses == {"delivered", "manual_review", "error"}


def test_prompts_are_byte_identical_to_the_originals():
    code = (
        "from stages.analyzer import ANALYZER_SYSTEM_PROMPT as a\n"
        "from stages.prompt_generator import V3_BASE_TEMPLATE as v, GENERATOR_SYSTEM_PROMPT as g\n"
        "from stages.validator import VALIDATOR_SYSTEM_PROMPT as r\n"
        "import json, sys; json.dump([a, v, g, r], sys.stdout)\n"
    )
    proc = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.')\n" + code],
                          cwd=LEGACY, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout) == [ANALYZER_SYSTEM_PROMPT, V3_BASE_TEMPLATE, GENERATOR_SYSTEM_PROMPT, VALIDATOR_SYSTEM_PROMPT]


# ── model upgrades: compatibility without changing decisions ──────────────────

def test_new_models_only_change_model_ids_and_token_room(legacy_results, photo_path, tmp_path, monkeypatch):
    """With claude-sonnet-5-5 + gpt-image-2.5-sunburst the decisions and prompts are unchanged."""
    spec = SCENARIOS["retry_then_deliver"]
    new = run_new(tmp_path, photo_path, spec, monkeypatch,
                  claude_model="claude-sonnet-5-5", image_model="gpt-image-2.5-sunburst")
    old = legacy_results["retry_then_deliver"]
    for key in ("status", "retries", "error", "analysis", "prompt", "validation", "generated_image_sha"):
        assert new["result"][key] == old["result"][key]
    for n, o in zip(new["claude_calls"], old["claude_calls"]):
        assert n["model"] == "claude-sonnet-5-5"
        assert n["max_tokens"] == 16000 and o["max_tokens"] in (2000, 4000)  # room for the model's own reasoning
        assert (n["system"], n["messages"]) == (o["system"], o["messages"])
    for n, o in zip(new["image_calls"], old["image_calls"]):
        assert n["model"] == "gpt-image-2.5-sunburst"
        assert {k: v for k, v in n.items() if k != "model"} == {k: v for k, v in o.items() if k != "model"}


def test_reply_after_a_thinking_block_is_read(photo_path, tmp_path, monkeypatch):
    """Newer models may put a thinking block before the text; the text is still used."""
    spec = {"claude": [say(analysis(), thinking_first=True), say("P", thinking_first=True),
                       say(review(), thinking_first=True)], "images": [img(1)]}
    new = run_new(tmp_path, photo_path, spec, monkeypatch, claude_model="claude-sonnet-5-5")
    assert new["result"]["status"] == "delivered"
    assert new["result"]["prompt"] == "P"


# ── proposals stay off unless enabled ─────────────────────────────────────────

def test_proposal_p1_fail_safe_review_is_off_by_default_and_works_when_enabled(photo_path, tmp_path, monkeypatch):
    spec = SCENARIOS["review_missing_cultural_counts_as_pass"]
    assert run_new(tmp_path / "a", photo_path, spec, monkeypatch)["result"]["status"] == "delivered"
    enabled = run_new(tmp_path / "b", photo_path, spec, monkeypatch, review_fail_safe=True)
    assert enabled["result"]["status"] == "manual_review"


def test_proposal_p2_input_fidelity_is_off_by_default_and_sent_when_enabled(photo_path, tmp_path, monkeypatch):
    spec = SCENARIOS["deliver_first_try"]
    assert "input_fidelity" not in run_new(tmp_path / "a", photo_path, spec, monkeypatch)["image_calls"][0]
    enabled = run_new(tmp_path / "b", photo_path, spec, monkeypatch, image_send_input_fidelity=True)
    assert enabled["image_calls"][0]["input_fidelity"] == "high"
