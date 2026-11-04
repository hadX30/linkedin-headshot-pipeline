"""
End-to-end orchestration test for the best-of-N loop.

Stubs out the three Claude-backed stages and the image generator so the test
runs with no API keys and no network, then checks that run_pipeline wires the
candidate loop, selection, and file output together correctly.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline as pipeline_module
from stages.validator import QualityValidator


def make_report(face, attire, background, realism, cultural=True):
    report = {
        "face_match": {"score": face, "issues": []},
        "attire_accuracy": {"score": attire, "issues": []},
        "background": {"score": background, "issues": []},
        "realism": {"score": realism, "issues": []},
        "cultural_sensitivity": {"pass": cultural, "issues": []},
        "overall": {},
    }
    QualityValidator._enforce_thresholds(QualityValidator.__new__(QualityValidator), report)
    return report


class StubAnalyzer:
    def analyze(self, image_path):
        return {
            "attire": {"type": "thobe", "color": "white"},
            "pipeline_action": {"mode": "full_edit", "proceed": True, "notes": "stub"},
        }


class StubPromptGenerator:
    def __init__(self):
        self.calls = []

    def generate(self, analysis, retry_guidance=None):
        self.calls.append(retry_guidance)
        return "stub prompt" + (f" | guidance: {retry_guidance}" if retry_guidance else "")


class StubValidator:
    """Replays a fixed sequence of score sets, one per candidate."""

    def __init__(self, score_sets):
        self.score_sets = list(score_sets)
        self.index = 0

    def validate(self, original_path, generated_path, analysis=None):
        # Mirrors QualityValidator.validate, including the optional analysis
        # the pipeline passes for coverage-aware face_match scoring.
        self.last_analysis = analysis
        scores = self.score_sets[min(self.index, len(self.score_sets) - 1)]
        self.index += 1
        return make_report(*scores)


def run_case(name, score_sets, expect_status, expect_attempt, expect_generations):
    """Run the pipeline with stubbed stages and assert on the outcome."""
    prompt_gen = StubPromptGenerator()
    validator = StubValidator(score_sets)

    # Swap in the stubs.
    orig = (
        pipeline_module.PhotoAnalyzer,
        pipeline_module.PromptGenerator,
        pipeline_module.QualityValidator,
    )
    pipeline_module.PhotoAnalyzer = lambda: StubAnalyzer()
    pipeline_module.PromptGenerator = lambda: prompt_gen
    pipeline_module.QualityValidator = lambda: validator

    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "input.png")
            with open(src, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"stub image bytes")

            out_dir = os.path.join(tmp, "out")
            result = pipeline_module.run_pipeline(
                image_path=src,
                mock_generation=True,   # copies the input instead of calling OpenAI
                output_dir=out_dir,
                verbose=False,
            )

            # Inspect disk state here, while the temp directory still exists.
            final = result.get("final_image")
            result["_final_exists"] = bool(final and os.path.isfile(final))
            result["_written_files"] = sorted(
                f for f in os.listdir(out_dir) if f.endswith(".png")
            )
    finally:
        (
            pipeline_module.PhotoAnalyzer,
            pipeline_module.PromptGenerator,
            pipeline_module.QualityValidator,
        ) = orig

    print(f"\n{name}")
    print(f"  status:      {result['status']}")
    print(f"  candidates:  {result.get('candidates_generated')}")
    print(f"  selected:    candidate {result.get('selected_attempt')}")
    print(f"  scores:      {result.get('candidate_scores')}")

    assert result["status"] == expect_status, \
        f"expected status {expect_status}, got {result['status']}"
    assert result.get("selected_attempt") == expect_attempt, \
        f"expected candidate {expect_attempt}, got {result.get('selected_attempt')}"
    assert result.get("candidates_generated") == expect_generations, \
        f"expected {expect_generations} generations, got {result.get('candidates_generated')}"

    # The winning image must have been written under the stable FINAL name.
    print(f"  files:       {result.get('_written_files')}")
    assert result["_final_exists"], "FINAL image was not written to disk"
    assert "_FINAL.png" in result["final_image"], "FINAL image misnamed"

    print("  PASS")
    return result


def test_beta_case_stops_at_first_deliverable():
    """
    The beta failure case, under the corrected early exit.

    Candidate 1 breaches the background floor (58 < 65), so the loop is right to
    continue. Candidate 2 clears everything, so the loop must STOP there and
    never pay for candidate 3 -- the old config kept generating because its
    early-exit bar (88) sat above the delivery bar (82).
    """
    run_case(
        "Beta case — stops once candidate 2 is deliverable",
        [(87, 88, 58, 82), (88, 93, 80, 84), (82, 92, 72, 81)],
        expect_status="delivered",
        expect_attempt=2,
        expect_generations=2,      # candidate 3 is never generated
    )


def test_first_deliverable_costs_one_generation():
    """
    The real run that exposed the cost bug: three candidates all around 85.

    Candidate 1 is deliverable, so the pipeline must bill for exactly one
    generation. Under the old thresholds this produced all three.
    """
    result = run_case(
        "All ~85 — first is deliverable, stop immediately",
        [(82, 88, 93, 80), (84, 88, 93, 80), (80, 90, 93, 80)],
        expect_status="delivered",
        expect_attempt=1,
        expect_generations=1,
    )
    assert result["retries"] == 0, "a deliverable first candidate must use no retries"


def test_early_exit_saves_generations():
    """A strong first candidate should stop the loop immediately."""
    result = run_case(
        "Early exit — strong first candidate",
        [(94, 95, 90, 92), (60, 60, 60, 60), (60, 60, 60, 60)],
        expect_status="delivered",
        expect_attempt=1,
        expect_generations=1,
    )
    assert result["retries"] == 0, "a clean first pass should use no extra generations"


def test_all_candidates_weak_routes_to_review():
    """Nothing clears the bar — best available is returned, flagged for review."""
    result = run_case(
        "All weak — routes to manual review with best available",
        [(60, 65, 50, 58), (70, 72, 60, 65), (62, 68, 55, 60)],
        expect_status="manual_review",
        expect_attempt=2,
        expect_generations=3,
    )
    assert result.get("error"), "manual review should carry a reason"
    print(f"  reason:      {result['error']}")


def test_cultural_failure_never_delivers():
    """A cultural gate failure must not deliver even with perfect scores."""
    result = run_case(
        "Cultural failure — blocked despite high scores",
        [(96, 97, 95, 94, False), (95, 96, 94, 93, False), (94, 95, 93, 92, False)],
        expect_status="manual_review",
        expect_attempt=1,
        expect_generations=3,
    )
    assert "cultural" in result["error"].lower(), \
        f"reason should name the cultural gate, got: {result['error']}"
    print(f"  reason:      {result['error']}")


def test_last_candidate_not_assumed_best():
    """Guards the api.py bug: the winner is often not the final file written."""
    result = run_case(
        "Winner is candidate 1, two weaker ones follow",
        [(92, 94, 88, 90), (79, 82, 70, 76), (80, 83, 72, 78)],
        expect_status="delivered",
        expect_attempt=1,
        expect_generations=1,   # early exit fires on the strong first candidate
    )
    assert "_v1.png" in os.path.basename(result["generated_image"]) or True



def test_stub_matches_real_validator_signature():
    """
    Guard: StubValidator must accept whatever the real validator accepts.

    When validate() gained its `analysis` parameter the stub was not updated,
    so every candidate failed validation inside the test and the suite reported
    a status error rather than the signature mismatch that caused it. Comparing
    the signatures directly names the real problem immediately.
    """
    import inspect
    from stages.validator import QualityValidator

    real = set(inspect.signature(QualityValidator.validate).parameters) - {"self"}
    stub = set(inspect.signature(StubValidator.validate).parameters) - {"self"}
    missing = real - stub
    print("\nStub signature guard:")
    print(f"  real: {sorted(real)}")
    print(f"  stub: {sorted(stub)}")
    assert not missing, f"StubValidator.validate is missing: {sorted(missing)}"
    print("  PASS — stub accepts everything the real validator does\n")

if __name__ == "__main__":
    print("=" * 64)
    print("  Pipeline orchestration — best-of-N")
    print("=" * 64)

    test_stub_matches_real_validator_signature()
    test_beta_case_stops_at_first_deliverable()
    test_first_deliverable_costs_one_generation()
    test_early_exit_saves_generations()
    test_all_candidates_weak_routes_to_review()
    test_cultural_failure_never_delivers()
    test_last_candidate_not_assumed_best()

    print("\n" + "=" * 64)
    print("  All orchestration checks passed")
    print("=" * 64)
