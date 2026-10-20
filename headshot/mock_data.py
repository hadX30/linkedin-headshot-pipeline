"""
Fixtures for mock mode: photo profiles (what the analyzer "sees") and scenarios
(how the generate/validate loop plays out). Nothing here calls an API.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .schemas import Analysis

# ── Profiles: plausible Stage 1 readings ──────────────────────────────────────

_BASE_QUALITY = {
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
            "facial_hair": {"present": True, "type": "full_beard", "description": "short trimmed full beard, dark brown", "description_ar": "لحية قصيرة مشذّبة بنية داكنة"},
            "expression": "slight_smile",
            "glasses": {"present": False, "type": "none", "description": "", "description_ar": ""},
            "accessories": [],
        },
        "attire": {
            "headwear": {"type": "shemagh_with_iqal", "color": "red and white", "description": "red and white houndstooth shemagh with black iqal", "description_ar": "شماغ أحمر وأبيض بعقال أسود"},
            "clothing": {"type": "thobe", "is_professional": True, "color": "white", "description": "white thobe, standard collar", "description_ar": "ثوب أبيض بياقة عادية"},
            "needs_wardrobe_change": False,
            "recommended_outfit": "Keep the thobe and shemagh as they are",
        },
        "photo_quality": {**_BASE_QUALITY, "lighting_quality": "natural_poor"},
    },
    "hijab_abaya": {
        "subject": {
            "apparent_gender": "female",
            "apparent_age_range": "young_adult",
            "skin_tone": "light medium",
            "facial_hair": {"present": False, "type": "none", "description": "", "description_ar": ""},
            "expression": "smiling",
            "glasses": {"present": False, "type": "none", "description": "", "description_ar": ""},
            "accessories": ["small drop earrings"],
        },
        "attire": {
            "headwear": {"type": "hijab", "color": "black", "description": "black chiffon hijab, wrapped and pinned under the chin", "description_ar": "حجاب أسود مثبّت تحت الذقن"},
            "clothing": {"type": "abaya", "is_professional": True, "color": "black", "description": "black open abaya", "description_ar": "عباءة سوداء"},
            "needs_wardrobe_change": False,
            "recommended_outfit": "Keep the hijab and abaya as they are",
        },
        "photo_quality": {**_BASE_QUALITY, "face_angle": "three_quarter"},
    },
    "niqab": {
        "subject": {
            "apparent_gender": "female",
            "apparent_age_range": "adult",
            "skin_tone": "not assessable",
            "facial_hair": {"present": False, "type": "none", "description": "", "description_ar": ""},
            "expression": "neutral",
            "glasses": {"present": False, "type": "none", "description": "", "description_ar": ""},
            "accessories": [],
        },
        "attire": {
            "headwear": {"type": "niqab", "color": "black", "description": "black niqab with eye opening", "description_ar": "نقاب أسود"},
            "clothing": {"type": "abaya", "is_professional": True, "color": "black", "description": "black abaya", "description_ar": "عباءة سوداء"},
            "needs_wardrobe_change": False,
            "recommended_outfit": "Keep everything as in the source",
        },
        "photo_quality": dict(_BASE_QUALITY),
    },
    "casual": {
        "subject": {
            "apparent_gender": "male",
            "apparent_age_range": "adult",
            "skin_tone": "medium",
            "facial_hair": {"present": True, "type": "stubble", "description": "light even stubble", "description_ar": "لحية خفيفة"},
            "expression": "neutral",
            "glasses": {"present": True, "type": "prescription", "description": "black rectangular frames", "description_ar": "نظارة سوداء مستطيلة"},
            "accessories": ["black watch on left wrist"],
        },
        "attire": {
            "headwear": {"type": "none", "color": "", "description": "", "description_ar": ""},
            "clothing": {"type": "casual", "is_professional": False, "color": "grey", "description": "grey crew-neck t-shirt", "description_ar": "تيشيرت رمادي"},
            "needs_wardrobe_change": True,
            "recommended_outfit": "White thobe with white shemagh and iqal",
        },
        "photo_quality": {**_BASE_QUALITY, "lighting_quality": "harsh", "background_type": "outdoor"},
    },
}


# ── Scenarios: how the loop plays out ─────────────────────────────────────────

@dataclass(frozen=True)
class AttemptScript:
    scores: tuple[int, int, int, int]  # face_match, attire_accuracy, background, realism
    cultural_pass: bool = True
    issues: dict[str, tuple[str, str]] = field(default_factory=dict)  # check -> (english, arabic)
    guidance: str = ""


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    reject_reason: str | None = None
    attempts: tuple[AttemptScript, ...] = ()
    failing_generations: tuple[int, ...] = ()  # attempt numbers whose image call fails

    def script_for(self, attempt: int) -> AttemptScript:
        return self.attempts[min(attempt, len(self.attempts)) - 1]


_PASS = AttemptScript((93, 95, 97, 90))

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("deliver", "Passes every check on the first attempt.", attempts=(_PASS,)),
        Scenario(
            "retry_then_deliver",
            "Face match fails once, the prompt is rewritten, the second attempt passes.",
            attempts=(
                AttemptScript(
                    (82, 94, 97, 88),
                    issues={"face_match": ("Eye colour drifted lighter than the original.", "لون العينين مال إلى الفاتح")},
                    guidance="State the eye colour explicitly as dark brown and repeat that facial features must not change.",
                ),
                AttemptScript((92, 94, 97, 89)),
            ),
        ),
        Scenario(
            "manual_review",
            "Cultural-sensitivity gate fails, so the run stops for a human.",
            attempts=(
                AttemptScript(
                    (90, 70, 96, 88),
                    cultural_pass=False,
                    issues={"attire_accuracy": ("Headwear coverage or pattern changed.", "تغيّرت تغطية أو نقشة غطاء الرأس")},
                ),
            ),
        ),
        Scenario(
            "exhausted",
            "Face match keeps failing; after the last attempt the run goes to manual review.",
            attempts=(
                AttemptScript((80, 94, 97, 88), issues={"face_match": ("Jawline softened.", "خط الفك صار أنعم")}, guidance="Describe the jawline as it is in the source."),
                AttemptScript((83, 94, 97, 88), issues={"face_match": ("Nose shape changed.", "تغيّر شكل الأنف")}, guidance="Keep the nose exactly as the source."),
                AttemptScript((84, 94, 97, 88), issues={"face_match": ("Face still drifts.", "الوجه ما زال مختلفاً")}),
            ),
        ),
        Scenario("rejected", "The analyzer rejects the photo (more than one face).", reject_reason="multiple_faces"),
        Scenario(
            "generation_retry",
            "The first image call fails with a temporary error, the second succeeds and passes.",
            attempts=(_PASS,),
            failing_generations=(1,),
        ),
        Scenario(
            "generation_error",
            "Every image call fails; the run ends in error.",
            attempts=(_PASS,),
            failing_generations=(1, 2, 3, 4, 5),
        ),
    )
}

AUTO_POOL = ("deliver", "retry_then_deliver")


def pick_scenario(requested: str | None, sha256: str) -> Scenario:
    """Use the requested scenario, or pick one deterministically from the photo hash."""
    if requested and requested != "auto":
        if requested not in SCENARIOS:
            raise ValueError(f"Unknown mock scenario {requested!r}. Choose from: auto, {', '.join(SCENARIOS)}")
        return SCENARIOS[requested]
    index = int(sha256[:8], 16) % len(AUTO_POOL)
    return SCENARIOS[AUTO_POOL[index]]


def pick_profile(sha256: str) -> str:
    names = sorted(PROFILES)
    return names[int(sha256[8:16], 16) % len(names)]


def analysis_for(profile: str, scenario: Scenario) -> Analysis:
    data = PROFILES[profile]
    if scenario.reject_reason:
        action = {"proceed": False, "mode": "reject", "reject_reason": scenario.reject_reason, "notes": "Mock rejection."}
        quality = {**data["photo_quality"], "multiple_faces": scenario.reject_reason == "multiple_faces"}
    else:
        locked = data["attire"]["headwear"]["type"] == "niqab"
        action = {
            "proceed": True,
            "mode": "reduced_edit" if locked else "full_edit",
            "reject_reason": "none",
            "notes": "Mock analysis.",
        }
        quality = data["photo_quality"]
    return Analysis.model_validate({**data, "photo_quality": quality, "pipeline_action": action})


def photo_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
