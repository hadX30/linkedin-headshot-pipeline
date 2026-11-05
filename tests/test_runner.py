"""The runner: what the apps record while the pipeline runs (traces, files, progress, outcome)."""

import pytest

import pipeline as team_pipeline
from headshot import mock
from headshot.runner import LiveUnavailable, create_run, current_mode, execute_run, install
from stages.analyzer import ANALYZER_SYSTEM_PROMPT
from stages.prompt_generator import GENERATOR_SYSTEM_PROMPT


def run(settings, store, photo, scenario, **kw):
    run_id = create_run(settings, store, photo, "photo.jpg", mode="mock", source="test", scenario=scenario)
    return run_id, execute_run(settings, store, run_id, **kw)


# Every decision below is made by the pipeline's own code; the scenarios only script the model replies.
EXPECTED = {
    # scenario: (status, selected candidate, generations started, reason)
    "deliver": ("delivered", 1, 1, "deliverable"),
    "floor_then_deliver": ("delivered", 2, 2, "deliverable"),
    "cultural_then_deliver": ("delivered", 2, 2, "deliverable"),
    "below_bar_then_deliver": ("delivered", 2, 2, "deliverable"),
    "no_deliverable": ("manual_review", 2, 3, "floors"),
    "cultural_fail": ("manual_review", 1, 3, "cultural"),
    "rejected": ("manual_review", None, 0, "analyzer_rejected"),
    "generation_failure": ("delivered", 2, 2, "deliverable"),
    "review_failure": ("delivered", 2, 2, "deliverable"),
    "generation_error": ("error", None, 3, None),
}


def test_every_scenario_is_listed():
    assert set(EXPECTED) == set(mock.SCENARIOS)


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
def test_mock_scenarios(settings, store, photo, scenario):
    status, selected, generations, reason = EXPECTED[scenario]
    run_id, result = run(settings, store, photo, scenario)
    record = store.get_run(run_id)
    assert (result["status"], result.get("selected_attempt"), record["attempts"], record["reason"]) == \
        (status, selected, generations, reason)
    assert record["status"] == status and record["stage"] is None and record["finished_at"]
    assert record["cost_estimated"] is True and record["cost_usd"] > 0

    candidates = record["result"]["candidates"]
    assert [c["selected"] for c in candidates] == [c["attempt"] == selected for c in candidates]
    if selected:
        assert record["output_file"] == f"attempt-{selected}.png"
        assert store.get_file(run_id, record["output_file"])[:8] == b"\x89PNG\r\n\x1a\n"


def test_candidate_records_carry_the_pipelines_verdicts(settings, store, photo):
    run_id, result = run(settings, store, photo, "floor_then_deliver")
    first, second = store.get_run(run_id)["result"]["candidates"]
    assert first["action"] == "retry" and first["floors_pass"] is False
    assert first["floors_failed"] == ["background (58 < 65)"] and first["checks"]["background"]["below_floor"]
    assert first["composite"] == result["candidate_scores"][0]["composite"] == 82.0
    assert first["checks"]["face_match"] | {"issues": None} == {
        "score": 87, "passed": True, "ideal": 85, "floor": 78, "weight": 0.35, "below_floor": False, "issues": None}
    assert first["guidance"].startswith("State that the source background")
    assert second["action"] == "deliver" and second["selected"] and second["composite"] == 87.5
    assert first["file"] == "attempt-1.png" and second["file"] == "attempt-2.png"


def test_result_keeps_the_pipelines_shape_with_file_names(settings, store, photo):
    run_id, result = run(settings, store, photo, "floor_then_deliver")
    stored = store.get_run(run_id)["result"]["pipeline"]
    assert stored["generated_image"] == stored["final_image"] == "attempt-2.png"
    assert result["final_image"].endswith("_headshot_FINAL.png")  # the pipeline's own value
    for key in ("status", "selected_attempt", "candidates_generated", "candidate_scores", "retries", "validation"):
        assert stored[key] == result[key]


def test_spans_follow_the_calls(settings, store, photo):
    run_id, _ = run(settings, store, photo, "floor_then_deliver")
    spans = store.list_spans(run_id)
    assert [(s["stage"], s["attempt"], s["status"]) for s in spans] == [
        ("analyze", 0, "ok"), ("prompt", 1, "ok"),
        ("generate", 1, "error"), ("generate", 1, "ok"), ("validate", 1, "ok"),  # input_fidelity rejected, then retried
        ("prompt", 2, "ok"),
        ("generate", 2, "error"), ("generate", 2, "ok"), ("validate", 2, "ok"),
    ]
    analyze, prompt = spans[0], spans[1]
    assert analyze["request"]["system"] == ANALYZER_SYSTEM_PROMPT
    assert analyze["request"]["content"][0] == {"image": "input", "media_type": "image/jpeg"}
    assert prompt["request"]["system"] == GENERATOR_SYSTEM_PROMPT
    assert "input_fidelity" in spans[2]["request"]["params"] and "input_fidelity" not in spans[3]["request"]["params"]
    assert spans[3]["request"]["image"] == "input"
    assert [p.get("image") for p in spans[8]["request"]["content"] if "image" in p] == ["input", "attempt-2"]
    assert "This is a RETRY." in spans[5]["request"]["content"][0]["text"]
    record = store.get_run(run_id)
    assert record["cost_usd"] == pytest.approx(sum(s["cost_usd"] or 0 for s in spans), abs=1e-6)


def test_covered_face_note_reaches_the_reviewer(settings, store, photo, monkeypatch):
    monkeypatch.setattr(mock, "pick_profile", lambda sha: "niqab")
    run_id, _ = run(settings, store, photo, "deliver")
    validate = [s for s in store.list_spans(run_id) if s["stage"] == "validate"][0]
    assert "FACE COVERAGE ADJUSTMENT" in validate["request"]["content"][-1]["text"]


def test_upload_is_stored_byte_for_byte(settings, store, photo):
    run_id, _ = run(settings, store, photo, "deliver")
    assert store.get_run(run_id)["input"]["stored_as"] == "input.jpg"
    assert store.get_file(run_id, "input.jpg") == photo


def test_stop_after_stage_returns_partial(settings, store, photo):
    _, result = run(settings, store, photo, "deliver", stop_after_stage=1)
    assert result["status"] == "partial" and result["analysis"] and result["prompt"] is None
    _, result = run(settings, store, photo, "deliver", stop_after_stage=2)
    assert result["status"] == "partial" and result["prompt"]


def test_pipeline_output_files_are_written_where_asked(settings, store, photo, tmp_path):
    run(settings, store, photo, "floor_then_deliver", output_dir=tmp_path / "out")
    names = sorted(p.name.split("_", 3)[3] for p in (tmp_path / "out").iterdir())  # <date>_<time>_photo_<name>
    assert names == ["analysis.json", "headshot_FINAL.png", "headshot_v1.png", "headshot_v2.png", "prompt.txt",
                     "prompt_retry1.txt", "validation_v1.json", "validation_v2.json"]


def test_the_seam_is_transparent_outside_a_run():
    install()
    assert issubclass(team_pipeline.PhotoAnalyzer, __import__("stages").analyzer.PhotoAnalyzer)
    # The team's own orchestration test, with the apps' classes installed.
    from . import test_pipeline_integration as team_tests

    team_tests.test_beta_case_stops_at_first_deliverable()
    team_tests.test_all_candidates_weak_routes_to_review()


def test_live_needs_keys(settings, store, photo):
    with pytest.raises(LiveUnavailable):
        create_run(settings, store, photo, "p.jpg", mode="live", source="test")
    # POST /v1/headshots does not pre-check keys, like the team's api.py
    assert create_run(settings, store, photo, "p.jpg", mode="live", source="test", require_keys=False)


def test_auto_scenario_is_deterministic(settings, store, photo):
    a = create_run(settings, store, photo, "p.jpg", mode="mock", source="test")
    b = create_run(settings, store, photo, "p.jpg", mode="mock", source="test")
    assert store.get_run(a)["scenario"] == store.get_run(b)["scenario"] in mock.AUTO_POOL


def test_saved_mode_overrides_env_default(settings, store):
    assert current_mode(settings, store) == "mock"
    store.set_setting("mode", "live")
    assert current_mode(settings, store) == "live"


def test_unfinished_runs_are_closed_on_restart(settings, store, photo):
    run_id = create_run(settings, store, photo, "p.jpg", mode="mock", source="test")
    assert store.fail_unfinished("restart") == 1
    assert store.get_run(run_id)["status"] == "error"


def test_stats_shape(settings, store, photo):
    for scenario in ("deliver", "rejected"):
        run(settings, store, photo, scenario)
    stats = store.stats("2000-01-01")
    # The Supabase trace_stats() function returns exactly these keys too.
    assert set(stats) == {"since", "runs", "finished", "delivery_rate", "avg_duration_ms", "avg_attempts",
                          "by_status", "by_mode", "cost", "by_stage", "by_model", "daily"}
    assert stats["runs"] == 2 and stats["delivery_rate"] == 0.5
    assert stats["by_status"] == {"delivered": 1, "manual_review": 1}
    assert stats["cost"]["live_usd"] == 0 and stats["cost"]["mock_estimated_usd"] > 0


def test_check_store_command_passes_on_sqlite(store, capsys):
    from headshot.cli import check_store

    assert check_store(store) == 0
    assert "All checks passed." in capsys.readouterr().out
    assert store.list_runs()[1] == 0
