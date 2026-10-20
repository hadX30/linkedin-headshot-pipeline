import pytest

from headshot.pipeline import LiveUnavailable, create_run, current_mode, execute_run

EXPECTED = {
    # scenario: (status, retries, error, reason, stages in call order; "!" marks a failed call)
    "deliver": ("delivered", 0, None, "passed", ["analyze", "prompt", "generate", "validate"]),
    "retry_then_deliver": ("delivered", 1, None, "passed",
                           ["analyze", "prompt", "generate", "validate", "prompt", "generate", "validate"]),
    "manual_review": ("manual_review", 1, None, "cultural", ["analyze", "prompt", "generate", "validate"]),
    "exhausted": ("manual_review", 3, None, "max_attempts",
                  ["analyze", "prompt", "generate", "validate", "prompt", "generate", "validate",
                   "prompt", "generate", "validate"]),
    "rejected": ("manual_review", 0, "Photo rejected by analyzer: multiple_faces", "analyzer_rejected", ["analyze"]),
    # Original behaviour: the attempt-1 error message stays in `error` even though the run was delivered
    # (proposal P5 would clear it).
    "generation_retry": ("delivered", 1, "Stage 3 failed (attempt 1): Image generation failed: mock image service "
                         "unavailable (scripted failure)", "passed", ["analyze", "prompt", "generate!", "generate", "validate"]),
    "generation_error": ("error", 0, "Stage 3 failed (attempt 3): Image generation failed: mock image service "
                         "unavailable (scripted failure)", None, ["analyze", "prompt", "generate!", "generate!", "generate!"]),
}


def run(settings, store, photo, scenario, **kw):
    run_id = create_run(settings, store, photo, "photo.jpg", mode="mock", source="test", scenario=scenario)
    return run_id, execute_run(settings, store, run_id, **kw)


@pytest.mark.parametrize("scenario", EXPECTED)
def test_mock_scenarios_follow_the_original_rules(settings, store, photo, scenario):
    status, retries, error, reason, stages = EXPECTED[scenario]
    run_id, result = run(settings, store, photo, scenario)
    record = store.get_run(run_id)
    spans = store.list_spans(run_id)

    assert (result["status"], result["retries"], result["error"]) == (status, retries, error)
    assert (record["status"], record["error"], record["reason"]) == (status, error, reason)
    assert [s["stage"] + ("!" if s["status"] == "error" else "") for s in spans] == stages
    assert [s["seq"] for s in spans] == list(range(1, len(spans) + 1))

    # Mock costs are estimates, never billed spend
    assert record["cost_estimated"] is True and all(s["cost_estimated"] for s in spans)
    ok_spans = [s for s in spans if s["status"] == "ok"]
    assert all(s["cost_usd"] > 0 for s in ok_spans)
    assert record["cost_usd"] == pytest.approx(sum(s["cost_usd"] for s in ok_spans), abs=1e-6)
    for s in ok_spans:
        assert s["request"] and s["response"] and s["usage"]


def test_upload_is_stored_byte_for_byte(settings, store, photo):
    run_id, _ = run(settings, store, photo, "deliver")
    record = store.get_run(run_id)
    assert record["input"]["stored_as"] == "input.jpg"
    assert store.get_file(run_id, "input.jpg") == photo


def test_traced_requests_use_the_original_prompts(settings, store, photo):
    from headshot.prompts import ANALYZER_SYSTEM_PROMPT, ANALYZER_USER_TEXT, GENERATOR_SYSTEM_PROMPT

    run_id, result = run(settings, store, photo, "retry_then_deliver")
    analyze, first_prompt, *_ = store.list_spans(run_id)
    assert analyze["request"]["system"] == ANALYZER_SYSTEM_PROMPT
    assert analyze["request"]["content"] == [{"image": "input", "media_type": "image/jpeg"}, {"text": ANALYZER_USER_TEXT}]
    assert first_prompt["request"]["system"] == GENERATOR_SYSTEM_PROMPT
    retry_prompt = [s for s in store.list_spans(run_id) if s["stage"] == "prompt"][1]
    assert "This is a RETRY." in retry_prompt["request"]["content"][0]["text"]
    assert result["prompt"] == first_prompt["response"]["text"]  # result keeps the first prompt, as before


def test_stop_after_stage_returns_partial(settings, store, photo):
    _, result = run(settings, store, photo, "deliver", stop_after_stage=1)
    assert result["status"] == "partial" and result["analysis"] and result["prompt"] is None
    _, result = run(settings, store, photo, "deliver", stop_after_stage=2)
    assert result["status"] == "partial" and result["prompt"]


def test_legacy_output_files_are_written(settings, store, photo, tmp_path):
    from headshot.pipeline import LegacyOutput

    _, result = run(settings, store, photo, "retry_then_deliver", output=LegacyOutput(tmp_path / "out", "photo.jpg"))
    names = sorted(p.name.split("_", 3)[3] for p in (tmp_path / "out").iterdir())
    assert names == ["analysis.json", "headshot_v1.png", "headshot_v2.png", "prompt.txt", "prompt_retry1.txt",
                     "validation_v1.json", "validation_v2.json"]
    assert result["generated_image"].endswith("_headshot_v2.png")


def test_live_needs_keys_on_new_entry_points(settings, store, photo):
    with pytest.raises(LiveUnavailable):
        create_run(settings, store, photo, "p.jpg", mode="live", source="test")
    # the original entry points did not pre-check keys; they still don't
    assert create_run(settings, store, photo, "p.jpg", mode="live", source="test", require_keys=False)


def test_auto_scenario_is_deterministic(settings, store, photo):
    a = create_run(settings, store, photo, "p.jpg", mode="mock", source="test")
    b = create_run(settings, store, photo, "p.jpg", mode="mock", source="test")
    assert store.get_run(a)["scenario"] == store.get_run(b)["scenario"]


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
