"""
The apps run the pipeline unchanged.

Each scenario runs pipeline.run_pipeline() twice on the same scripted model replies:
  1. alone, in a fresh interpreter (tests/run_team_pipeline.py), nothing plugged in;
  2. through headshot.runner, the way the API, the dashboard and the CLI run it
     (tracing, progress reporting, the trace store).
Both must return the same result, send the same requests to Claude and the image
API, and end on the same final image.
"""

import dataclasses
import json
import os
import subprocess
import sys

import anthropic
import openai
import pytest

from headshot.runner import create_run, execute_run

from . import fakes
from .conftest import make_photo

HERE = os.path.dirname(os.path.abspath(__file__))


def analysis(proceed=True, reject_reason=None, headwear="shemagh_with_iqal"):
    return json.dumps({
        "subject": {"apparent_gender": "male", "apparent_age_range": "adult", "skin_tone": "medium",
                    "facial_hair": {"present": True, "type": "full_beard", "description": "short dark beard"},
                    "expression": "slight_smile", "glasses": {"present": False}, "visible_accessories": []},
        "attire": {"headwear": {"type": headwear, "color": "red and white", "description": f"{headwear} as worn"},
                   "clothing": {"type": "thobe", "is_professional": True, "color": "white", "description": "white thobe"},
                   "needs_wardrobe_change": False, "recommended_outfit": "Keep as is"},
        "photo_quality": {"resolution_sufficient": True, "face_visible": True, "face_angle": "frontal",
                          "lighting_quality": "natural_good", "has_beauty_filters": False, "multiple_faces": False,
                          "background_type": "indoor"},
        "pipeline_action": {"proceed": proceed, "mode": "full_edit" if proceed else "reject",
                            "reject_reason": reject_reason, "notes": "test"},
    })


def review(face, attire, background, realism, cultural=True, guidance=None):
    report = {name: {"score": score, "pass": True, "issues": []}
              for name, score in zip(("face_match", "attire_accuracy", "background", "realism"),
                                     (face, attire, background, realism))}
    report["cultural_sensitivity"] = {"pass": cultural, "issues": [] if cultural else ["coverage changed"]}
    report["overall"] = {"pass": True, "action": "deliver", **({"retry_guidance": guidance} if guidance else {})}
    return "```json\n" + json.dumps(report) + "\n```"


A, P = {"text": analysis()}, {"text": "Edit the photo. Keep the face."}
SCENARIOS = {
    "first_candidate_delivers": {"claude": [A, P, {"text": review(90, 93, 88, 87)}], "images": [{"png": 1}]},
    "beta_case_floor_then_deliver": {
        "claude": [A, P, {"text": review(87, 88, 58, 82, guidance="Replace the café fully.")},
                   {"text": "Edit again; replace the café."}, {"text": review(88, 93, 80, 84)}],
        "images": [{"png": 1}, {"png": 2}],
    },
    "cultural_then_deliver": {
        "claude": [A, P, {"text": review(95, 60, 90, 90, cultural=False)}, {"text": "Retry prompt"},
                   {"text": review(89, 91, 86, 86)}],
        "images": [{"png": 1}, {"png": 2}],
    },
    "no_deliverable_best_of_three": {
        "claude": [A, P, {"text": review(60, 65, 50, 58)}, {"text": "R1"}, {"text": review(70, 72, 60, 65)},
                   {"text": "R2"}, {"text": review(62, 68, 55, 60)}],
        "images": [{"png": 1}, {"png": 2}, {"png": 3}],
    },
    "covered_face_note": {"claude": [{"text": analysis(headwear="niqab")}, P, {"text": review(84, 92, 85, 86)}],
                          "images": [{"png": 1}]},
    "analyzer_rejects": {"claude": [{"text": analysis(proceed=False, reject_reason="multiple_faces")}], "images": []},
    "generation_fails_once": {
        "claude": [A, P, {"text": review(90, 93, 88, 87)}],
        "images": [{"raise": "server error"}, {"png": 2}],
    },
    "review_unparsable_once": {
        "claude": [A, P, {"text": "not json"}, {"text": review(90, 93, 88, 87)}],
        "images": [{"png": 1}, {"png": 2}],
    },
    "every_generation_fails": {"claude": [A, P], "images": [{"raise": "down"}, {"raise": "down"}, {"raise": "down"}]},
    "prompt_stage_fails": {"claude": [A, {"raise": "overloaded"}], "images": []},
    "stop_after_stage_2": {"claude": [A, P], "images": [], "stage": 2},
}

COMPARED = ("status", "error", "retries", "selected_attempt", "candidates_generated", "candidate_scores",
            "validation", "analysis", "prompt")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_apps_run_the_pipeline_unchanged(name, tmp_path, settings, store, monkeypatch):
    spec = SCENARIOS[name]
    photo = make_photo()
    image = tmp_path / "photo.jpg"
    image.write_bytes(photo)

    # 1. The pipeline alone, in a fresh interpreter.
    spec_file, out_file = tmp_path / "spec.json", tmp_path / "alone.json"
    spec_file.write_text(json.dumps({**spec, "image": str(image), "out_dir": str(tmp_path / "alone")}))
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "run_team_pipeline.py"), str(spec_file), str(out_file)],
                   check=True, capture_output=True, text=True, timeout=120)
    alone = json.loads(out_file.read_text())

    # 2. The same pipeline, run by the apps (live mode, against the same scripted clients).
    monkeypatch.setattr(anthropic, "Anthropic", fakes.FakeAnthropic)
    monkeypatch.setattr(openai, "OpenAI", fakes.FakeOpenAI)
    fakes.Script.load(spec)
    live = dataclasses.replace(settings, mode="live")
    run_id = create_run(live, store, photo, "photo.jpg", mode="live", source="test", require_keys=False)
    result = execute_run(live, store, run_id, stop_after_stage=spec.get("stage", 4), output_dir=tmp_path / "apps")

    for key in COMPARED:
        assert result.get(key) == alone["result"].get(key), key
    assert fakes.Script.claude_calls == alone["claude_calls"]
    assert fakes.Script.image_calls == alone["image_calls"]

    run = store.get_run(run_id)
    stored = store.get_file(run_id, run["output_file"]) if run["output_file"] else None
    assert (fakes.sha(stored) if stored else None) == alone["final_sha"]
    assert run["status"] == alone["result"]["status"]
