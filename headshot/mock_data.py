"""
Fixtures for mock mode (tests and demos only; never the default).

Mock replies use exactly the JSON shapes the original analyzer and validator prompts
ask for, so mock runs go through the same original parsing and decision code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

# ── Profiles: plausible Stage 1 replies, in the original analyzer shape ───────

_QUALITY = {
    "resolution_sufficient": True,
    "face_visible": True,
    "face_angle": "frontal",
    "lighting_quality": "natural_good",
    "has_beauty_filters": False,
    "multiple_faces": False,
    "background_type": "indoor",
}

PROFILES: dict[str, dict] = {
    "shemagh_thobe": {
        "subject": {
            "apparent_gender": "male",
            "apparent_age_range": "adult",
            "skin_tone": "medium olive",
            "facial_hair": {"present": True, "type": "full_beard", "description": "short trimmed full beard, dark brown"},
            "expression": "slight_smile",
            "glasses": {"present": False, "type": "prescription", "frame_description": ""},
            "visible_accessories": [],
        },
        "attire": {
            "headwear": {"type": "shemagh_with_iqal", "color": "red and white houndstooth", "description": "red and white houndstooth shemagh with black iqal"},
            "clothing": {"type": "thobe", "is_professional": True, "color": "white", "description": "white thobe with standard collar"},
            "needs_wardrobe_change": False,
            "recommended_outfit": "Keep current thobe and shemagh as-is",
        },
        "photo_quality": {**_QUALITY, "lighting_quality": "natural_poor"},
    },
    "hijab_abaya": {
        "subject": {
            "apparent_gender": "female",
            "apparent_age_range": "young_adult",
            "skin_tone": "light medium",
            "facial_hair": {"present": False, "type": "clean_shaven", "description": ""},
            "expression": "smiling",
            "glasses": {"present": False, "type": "prescription", "frame_description": ""},
            "visible_accessories": ["small drop earrings"],
        },
        "attire": {
            "headwear": {"type": "hijab", "color": "black", "description": "black chiffon hijab, wrapped and pinned under the chin"},
            "clothing": {"type": "abaya", "is_professional": True, "color": "black", "description": "black open abaya"},
            "needs_wardrobe_change": False,
            "recommended_outfit": "Keep the hijab and abaya as they are",
        },
        "photo_quality": {**_QUALITY, "face_angle": "three_quarter"},
    },
    "niqab": {
        "subject": {
            "apparent_gender": "female",
            "apparent_age_range": "adult",
            "skin_tone": "not assessable",
            "facial_hair": {"present": False, "type": "clean_shaven", "description": ""},
            "expression": "neutral",
            "glasses": {"present": False, "type": "prescription", "frame_description": ""},
            "visible_accessories": [],
        },
        "attire": {
            "headwear": {"type": "niqab", "color": "black", "description": "black niqab with eye opening"},
            "clothing": {"type": "abaya", "is_professional": True, "color": "black", "description": "black abaya"},
            "needs_wardrobe_change": False,
            "recommended_outfit": "Keep everything as in the source",
        },
        "photo_quality": dict(_QUALITY),
    },
    "casual": {
        "subject": {
            "apparent_gender": "male",
            "apparent_age_range": "adult",
            "skin_tone": "medium",
            "facial_hair": {"present": True, "type": "stubble", "description": "light even stubble"},
            "expression": "neutral",
            "glasses": {"present": True, "type": "prescription", "frame_description": "black rectangular frames"},
            "visible_accessories": ["black watch on left wrist"],
        },
        "attire": {
            "headwear": {"type": "none", "color": "", "description": ""},
            "clothing": {"type": "casual", "is_professional": False, "color": "grey", "description": "grey crew-neck t-shirt"},
            "needs_wardrobe_change": True,
            "recommended_outfit": "White thobe with white shemagh and iqal",
        },
        "photo_quality": {**_QUALITY, "lighting_quality": "harsh", "background_type": "outdoor"},
    },
}


# ── Scenarios: how the generate/validate loop plays out ───────────────────────

@dataclass(frozen=True)
class AttemptScript:
    scores: tuple[int, int, int, int]  # face_match, attire_accuracy, background, realism
    cultural_pass: bool = True
    issues: dict[str, str] = field(default_factory=dict)  # check -> issue text
    guidance: str = ""


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    reject_reason: str | None = None
    attempts: tuple[AttemptScript, ...] = ()
    failing_generations: tuple[int, ...] = ()  # attempt numbers whose image call fails

    def script_for(self, attempt: int) -> AttemptScript:
        return self.attempts[min(max(attempt, 1), len(self.attempts)) - 1]


_PASS = AttemptScript((93, 95, 97, 90))

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("deliver", "Passes every check on the first attempt.", attempts=(_PASS,)),
        Scenario(
            "retry_then_deliver",
            "Face match fails once, the prompt is rewritten with the reviewer's guidance, the second attempt passes.",
            attempts=(
                AttemptScript((82, 94, 97, 88), issues={"face_match": "eye color shifted from brown to hazel"},
                              guidance="Increase face_id_scale to 0.9. Add negative prompt: hazel eyes."),
                AttemptScript((92, 94, 97, 89)),
            ),
        ),
        Scenario(
            "manual_review",
            "Cultural-sensitivity check fails, so the run goes to manual review.",
            attempts=(AttemptScript((90, 70, 96, 88), cultural_pass=False,
                                    issues={"attire_accuracy": "hijab coverage changed — hair visible at temple"}),),
        ),
        Scenario(
            "exhausted",
            "Face match keeps failing; after the last attempt the run goes to manual review.",
            attempts=(
                AttemptScript((80, 94, 97, 88), issues={"face_match": "jawline softened"}, guidance="Keep the jawline exactly as the source."),
                AttemptScript((83, 94, 97, 88), issues={"face_match": "nose shape changed"}, guidance="Keep the nose exactly as the source."),
                AttemptScript((84, 94, 97, 88), issues={"face_match": "face still drifts"}),
            ),
        ),
        Scenario("rejected", "The analyzer rejects the photo (more than one face): manual review.", reject_reason="multiple_faces"),
        Scenario("generation_retry", "The first image call fails, the second succeeds and passes.",
                 attempts=(_PASS,), failing_generations=(1,)),
        Scenario("generation_error", "Every image call fails; the run ends in error.",
                 attempts=(_PASS,), failing_generations=(1, 2, 3, 4, 5)),
    )
}

AUTO_POOL = ("deliver", "retry_then_deliver")


def pick_scenario(requested: str | None, sha256: str) -> Scenario:
    """Use the requested scenario, or pick one deterministically from the photo hash."""
    if requested and requested != "auto":
        if requested not in SCENARIOS:
            raise ValueError(f"Unknown mock scenario {requested!r}. Choose from: auto, {', '.join(SCENARIOS)}")
        return SCENARIOS[requested]
    return SCENARIOS[AUTO_POOL[int(sha256[:8], 16) % len(AUTO_POOL)]]


def pick_profile(sha256: str) -> str:
    names = sorted(PROFILES)
    return names[int(sha256[8:16], 16) % len(names)]


def analysis_reply(profile: str, scenario: Scenario) -> str:
    """The analyzer's JSON reply, as text."""
    data = json.loads(json.dumps(PROFILES[profile]))
    if scenario.reject_reason:
        data["photo_quality"]["multiple_faces"] = scenario.reject_reason == "multiple_faces"
        action = {"proceed": False, "mode": "reject", "reject_reason": scenario.reject_reason, "notes": "Mock rejection."}
    else:
        action = {"proceed": True, "mode": "full_edit", "reject_reason": None, "notes": "Mock analysis."}
    data["pipeline_action"] = action
    return json.dumps(data, indent=2, ensure_ascii=False)


def validation_reply(scenario: Scenario, attempt: int) -> str:
    """The validator's JSON reply, as text (its own pass/overall are recomputed by the pipeline)."""
    script = scenario.script_for(attempt)
    names = ("face_match", "attire_accuracy", "background", "realism")
    report = {name: {"score": score, "pass": True, "issues": [script.issues[name]] if name in script.issues else []}
              for name, score in zip(names, script.scores)}
    report["cultural_sensitivity"] = {"pass": script.cultural_pass,
                                      "issues": [] if script.cultural_pass else ["traditional attire was altered"]}
    report["overall"] = {"pass": True, "action": "deliver", "retry_guidance": script.guidance or ""}
    if not script.guidance:
        report["overall"].pop("retry_guidance")
    return json.dumps(report, indent=2, ensure_ascii=False)


def photo_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
