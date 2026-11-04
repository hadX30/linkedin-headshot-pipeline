"""
Replays the beta failure case through the new decision logic.

The case: a thobe photo that produced three candidates, all of which the old
pipeline rejected, exhausting retries and routing to manual review even though
candidate 2 was an excellent result.

    candidate 1: face 87, attire 88, background 58, realism 82
    candidate 2: face 88, attire 93, background 80, realism 84
    candidate 3: face 82, attire 92, background 72, realism 81

Under the old rules every candidate failed because background never reached 95.
Under the new rules candidate 2 should win and be delivered.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    COMPOSITE_DELIVER_THRESHOLD,
    EARLY_EXIT_COMPOSITE,
    HARD_FLOORS,
)
from stages.validator import QualityValidator, select_best_candidate


def build_report(face, attire, background, realism, cultural=True):
    """Build a validator report the way the model would return it."""
    report = {
        "face_match": {"score": face, "issues": []},
        "attire_accuracy": {"score": attire, "issues": []},
        "background": {"score": background, "issues": []},
        "realism": {"score": realism, "issues": []},
        "cultural_sensitivity": {"pass": cultural, "issues": []},
        "overall": {},
    }
    # Run the real threshold/composite logic over it.
    QualityValidator._enforce_thresholds(QualityValidator.__new__(QualityValidator), report)
    return report


BETA_CASE = [
    (1, 87, 88, 58, 82),
    (2, 88, 93, 80, 84),
    (3, 82, 92, 72, 81),
]


def test_beta_case_selects_candidate_2():
    candidates = []
    for attempt, face, attire, bg, realism in BETA_CASE:
        candidates.append(
            {
                "attempt": attempt,
                "path": f"/fake/v{attempt}.png",
                "validation": build_report(face, attire, bg, realism),
            }
        )

    print("\nBeta case — per-candidate scoring under the new logic:")
    for c in candidates:
        o = c["validation"]["overall"]
        print(
            f"  candidate {c['attempt']}: composite {o['composite']:>5} | "
            f"floors_pass={str(o['floors_pass']):<5} | action={o['action']}"
        )

    best = select_best_candidate(candidates)
    print(f"\n  selected: candidate {best['attempt']} (deliverable={best['deliverable']})")

    assert best["attempt"] == 2, f"expected candidate 2, got {best['attempt']}"
    assert best["deliverable"] is True, "candidate 2 should be deliverable"
    print("  PASS — candidate 2 selected and delivered\n")


def test_face_floor_blocks_delivery():
    """A high composite must not rescue a face that drifted too far."""
    # Strong everywhere except the face, which is below the 78 floor.
    report = build_report(face=70, attire=95, background=92, realism=90)
    o = report["overall"]
    print("Face-floor guard:")
    print(f"  composite {o['composite']} but floors_pass={o['floors_pass']}")
    assert o["floors_pass"] is False, "sub-floor face must fail the floors"
    assert o["action"] != "deliver", "must not deliver with a broken face match"
    print("  PASS — blocked despite a high composite\n")


def test_unmodified_photo_is_never_delivered():
    """
    Regression guard for a real false positive found in the output folder.

    A mock run wrote three byte-identical copies of the ORIGINAL photo — café
    background and all. Because the image was literally the input, face and
    attire scored 99, and the composite reached 84.8 against an 82 bar. Without
    a background floor this ships a headshot with a coffee shop behind the
    subject. The background floor must catch it.
    """
    report = build_report(face=99, attire=99, background=40, realism=72)
    o = report["overall"]
    print("Unmodified-photo guard (real case: face 99, attire 99, bg 40):")
    print(f"  composite {o['composite']} | floors_pass={o['floors_pass']}")
    print(f"  floors_failed: {o['floors_failed']}")
    assert o["composite"] >= COMPOSITE_DELIVER_THRESHOLD, (
        "this case must still clear the composite bar — that is precisely why "
        "a weight alone was insufficient and a floor was required"
    )
    assert o["floors_pass"] is False, "background floor must reject a visible original environment"
    assert o["action"] != "deliver", "an unmodified photo must never be delivered"
    print("  PASS — blocked by the background floor despite composite 84.8\n")


def test_clean_but_flawed_background_still_passes():
    """The background floor must not reject a clean backdrop with a minor flaw."""
    # 72 = "clean replacement with a visible flaw" per the rubric — acceptable.
    report = build_report(face=88, attire=92, background=72, realism=84)
    o = report["overall"]
    print("Flawed-but-clean background:")
    print(f"  composite {o['composite']} | floors_pass={o['floors_pass']} | action={o['action']}")
    assert o["floors_pass"] is True, "a clean backdrop at 72 must clear the floor"
    assert o["action"] == "deliver", "should deliver on a minor background flaw"
    print("  PASS — floor does not over-reject\n")


def test_cultural_gate_is_absolute():
    """Cultural failure routes to manual review regardless of every other score."""
    report = build_report(face=95, attire=98, background=96, realism=94, cultural=False)
    o = report["overall"]
    print("Cultural gate:")
    print(f"  composite {o['composite']} → action={o['action']}")
    assert o["action"] == "manual_review", "cultural failure must force manual review"
    print("  PASS — manual review enforced\n")


def test_no_deliverable_returns_best_available():
    """When nothing clears the bar, the studio still gets the best image."""
    candidates = [
        {"attempt": 1, "path": "a.png", "validation": build_report(60, 70, 50, 60)},
        {"attempt": 2, "path": "b.png", "validation": build_report(72, 75, 65, 70)},
    ]
    best = select_best_candidate(candidates)
    print("No-deliverable fallback:")
    print(f"  selected candidate {best['attempt']}, deliverable={best['deliverable']}")
    assert best["attempt"] == 2, "should return the strongest available"
    assert best["deliverable"] is False, "must be flagged as not deliverable"
    print("  PASS — best available returned, flagged for review\n")


def test_old_logic_comparison():
    """Show explicitly what the old all-thresholds-must-pass rule did."""
    old_thresholds = {"face_match": 85, "attire_accuracy": 90, "background": 95, "realism": 80}
    print("Old logic (every check must clear its own threshold):")
    any_passed = False
    for attempt, face, attire, bg, realism in BETA_CASE:
        scores = {
            "face_match": face,
            "attire_accuracy": attire,
            "background": bg,
            "realism": realism,
        }
        failed = [k for k, v in scores.items() if v < old_thresholds[k]]
        verdict = "deliver" if not failed else "retry"
        any_passed = any_passed or not failed
        print(f"  candidate {attempt}: {verdict} (failed: {', '.join(failed) or 'none'})")
    assert not any_passed, "the beta case should fail entirely under old rules"
    print("  → all three rejected, retries exhausted, manual review\n")


if __name__ == "__main__":
    print("=" * 64)
    print("  Decision logic verification")
    print(f"  deliver bar: composite >= {COMPOSITE_DELIVER_THRESHOLD}")
    print(f"  early exit:  composite >= {EARLY_EXIT_COMPOSITE}")
    print(f"  hard floors: {HARD_FLOORS}")
    print("=" * 64)

    test_old_logic_comparison()
    test_beta_case_selects_candidate_2()
    test_face_floor_blocks_delivery()
    test_unmodified_photo_is_never_delivered()
    test_clean_but_flawed_background_still_passes()
    test_cultural_gate_is_absolute()
    test_no_deliverable_returns_best_available()

    print("=" * 64)
    print("  All checks passed")
    print("=" * 64)
