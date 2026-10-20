import pytest

from headshot.pipeline import LiveUnavailable, create_run, current_mode, execute_run

EXPECTED = {
    # scenario: (status, reason, attempts, stages in call order; "!" marks a failed call)
    "deliver": ("delivered", "passed", 1, ["analyze", "prompt", "generate", "validate"]),
    "retry_then_deliver": ("delivered", "passed", 2,
                           ["analyze", "prompt", "generate", "validate", "prompt", "generate", "validate"]),
    "manual_review": ("manual_review", "cultural", 1, ["analyze", "prompt", "generate", "validate"]),
    "exhausted": ("manual_review", "max_attempts", 3,
                  ["analyze", "prompt", "generate", "validate", "prompt", "generate", "validate",
                   "prompt", "generate", "validate"]),
    "rejected": ("rejected", "multiple_faces", 0, ["analyze"]),
    "generation_retry": ("delivered", "passed", 2, ["analyze", "prompt", "generate!", "generate", "validate"]),
    "generation_error": ("error", None, 3, ["analyze", "prompt", "generate!", "generate!", "generate!"]),
}


@pytest.mark.parametrize("scenario", EXPECTED)
def test_mock_scenarios_end_to_end(settings, store, photo, scenario):
    status, reason, attempts, stages = EXPECTED[scenario]
    run_id, _ = create_run(settings, store, photo, mode="mock", source="test", scenario=scenario)
    run = execute_run(settings, store, run_id)
    spans = store.list_spans(run_id)

    assert run["status"] == status
    assert run["reason"] == reason
    assert run["attempts"] == attempts
    assert [s["stage"] + ("!" if s["status"] == "error" else "") for s in spans] == stages
    assert [s["seq"] for s in spans] == list(range(1, len(spans) + 1))

    # Mock costs are estimates of the live models, never real spend
    assert run["cost_estimated"] is True
    assert all(s["cost_estimated"] for s in spans)
    ok_spans = [s for s in spans if s["status"] == "ok"]
    assert all(s["cost_usd"] > 0 for s in ok_spans)
    assert run["cost_usd"] == pytest.approx(sum(s["cost_usd"] for s in ok_spans), abs=1e-6)

    # Every successful call recorded what it sent and got back
    for s in ok_spans:
        assert s["request"] and s["response"] and s["usage"]
    if status in ("delivered", "manual_review"):
        assert store.get_file(run_id, run["output_file"]) is not None


def test_prompt_and_response_are_traced(settings, store, photo):
    run_id, _ = create_run(settings, store, photo, mode="mock", source="test", scenario="retry_then_deliver")
    execute_run(settings, store, run_id)
    spans = store.list_spans(run_id)
    analyze, first_prompt, *_ = spans
    assert "photo-analysis stage" in analyze["request"]["system"]
    assert analyze["response"]["parsed"]["attire"]["headwear"]["type"]
    retry_prompt = [s for s in spans if s["stage"] == "prompt"][1]
    assert "Retry feedback from the reviewer" in retry_prompt["request"]["text"]
    assert "Reviewer feedback addressed" in retry_prompt["response"]["text"]
    generate = next(s for s in spans if s["stage"] == "generate")
    assert generate["request"]["prompt"] == first_prompt["response"]["text"]
    assert generate["response"]["file"] == "attempt-1.png"


def test_live_needs_keys(settings, store, photo):
    with pytest.raises(LiveUnavailable):
        create_run(settings, store, photo, mode="live", source="test")


def test_auto_scenario_is_deterministic(settings, store, photo):
    a, _ = create_run(settings, store, photo, mode="mock", source="test")
    b, _ = create_run(settings, store, photo, mode="mock", source="test")
    assert store.get_run(a)["scenario"] == store.get_run(b)["scenario"]


def test_saved_mode_overrides_env_default(settings, store):
    assert current_mode(settings, store) == "mock"
    store.set_setting("mode", "live")
    assert current_mode(settings, store) == "live"


def test_unfinished_runs_are_closed_on_restart(settings, store, photo):
    run_id, _ = create_run(settings, store, photo, mode="mock", source="test")
    assert store.fail_unfinished("restart") == 1
    assert store.get_run(run_id)["status"] == "error"


def test_stats_shape(settings, store, photo):
    for scenario in ("deliver", "rejected"):
        run_id, _ = create_run(settings, store, photo, mode="mock", source="test", scenario=scenario)
        execute_run(settings, store, run_id)
    stats = store.stats("2000-01-01")
    # The Supabase trace_stats() function returns exactly these keys too.
    assert set(stats) == {"since", "runs", "finished", "delivery_rate", "avg_duration_ms", "avg_attempts",
                          "by_status", "by_mode", "cost", "by_stage", "by_model", "daily"}
    assert set(stats["cost"]) == {"live_usd", "mock_estimated_usd", "avg_per_delivered_live", "avg_per_delivered_mock"}
    assert stats["runs"] == 2
    assert stats["delivery_rate"] == 0.5
    assert stats["cost"]["live_usd"] == 0
    assert stats["cost"]["mock_estimated_usd"] > 0


def test_check_store_command_passes_on_sqlite(store, capsys):
    from headshot.cli import check_store

    assert check_store(store) == 0
    assert "All checks passed." in capsys.readouterr().out
    assert store.list_runs()[1] == 0  # cleaned up after itself
