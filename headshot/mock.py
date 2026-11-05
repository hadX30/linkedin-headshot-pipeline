"""
Mock mode (tests and demos; never the default): scripted stand-ins for the Claude
and GPT Image clients. They return replies in the shapes the pipeline's prompts ask
for, draw a stand-in image locally, and report the token usage a live call would
roughly have had, so traces show an *estimated* cost without spending anything.

Only the model replies are scripted. Every decision (floors, composite, cultural
gate, retries, which candidate is selected) is made by the pipeline's own code.
"""

from __future__ import annotations

import base64
import json
import math
import time
from dataclasses import dataclass, field
from types import SimpleNamespace as NS
from typing import Any, Protocol

from .imaging import image_size, render_mock_headshot

# ── Profiles: plausible Stage 1 replies, in the analyzer's JSON shape ──────────

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
            "recommended_outfit": "A tailored dark suit jacket with a collared white shirt",
        },
        "photo_quality": {**_QUALITY, "lighting_quality": "harsh", "background_type": "outdoor"},
    },
}




# ── Scenarios: what the reviewer says about each candidate ────────────────────

@dataclass(frozen=True)
class Review:
    scores: tuple[int, int, int, int] = (91, 93, 88, 87)  # face_match, attire_accuracy, background, realism
    cultural: bool = True
    issues: dict[str, str] = field(default_factory=dict)  # check -> issue text
    cultural_issue: str = "Traditional attire was altered."
    guidance: str = ""
    malformed: bool = False  # the reply is not JSON


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    reject_reason: str | None = None
    reviews: tuple[Review, ...] = (Review(),)  # one per candidate; the last one repeats
    failing_generations: tuple[int, ...] = ()  # candidates whose image call fails

    def review_for(self, candidate: int) -> Review:
        return self.reviews[min(max(candidate, 1), len(self.reviews)) - 1]


_GOOD = Review()

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("deliver", "The first candidate clears every floor and the delivery bar, so the loop stops after one image."),
        Scenario(
            "floor_then_deliver",
            "Candidate 1 still shows the original background (below the background floor); candidate 2 is delivered.",
            reviews=(
                Review((87, 88, 58, 82), issues={"background": "Original café wall still visible behind the left shoulder"},
                       guidance="State that the source background is replaced entirely, including the area between the arm and torso."),
                Review((88, 93, 80, 84)),
            ),
        ),
        Scenario(
            "cultural_then_deliver",
            "Candidate 1 adds a thobe and shemagh the person was not wearing (cultural gate); candidate 2 is delivered.",
            reviews=(
                Review((90, 60, 88, 86), cultural=False, cultural_issue="A thobe and shemagh were added that the person was not wearing.",
                       issues={"attire_accuracy": "Casual sweatshirt replaced with traditional dress"},
                       guidance="Keep the wardrobe change neutral: a dark blazer over a white collared shirt."),
                Review((89, 91, 86, 86)),
            ),
        ),
        Scenario(
            "below_bar_then_deliver",
            "Candidate 1 clears the floors but its composite is below the delivery bar; candidate 2 is delivered.",
            reviews=(
                Review((80, 82, 70, 76), issues={"realism": "Skin looks smoothed on the cheeks"},
                       guidance="Ask for visible skin texture on the cheeks and forehead."),
                Review((88, 92, 84, 86)),
            ),
        ),
        Scenario(
            "no_deliverable",
            "No candidate reaches the bar in three tries; the best one goes to manual review.",
            reviews=(Review((60, 65, 50, 58)), Review((70, 72, 60, 65)), Review((62, 68, 55, 60))),
        ),
        Scenario(
            "cultural_fail",
            "Every candidate changes the head covering (cultural gate); the run goes to manual review.",
            reviews=(Review((92, 70, 90, 88), cultural=False, cultural_issue="Hijab coverage changed: hair visible at the temple."),),
        ),
        Scenario("rejected", "The analyzer rejects the photo (more than one face); nothing is generated.",
                 reject_reason="multiple_faces"),
        Scenario("generation_failure", "The first image call fails; that candidate is skipped and the next one is delivered.",
                 failing_generations=(1,)),
        Scenario("review_failure", "The reviewer's first reply is not valid JSON; that candidate is skipped and the next one is delivered.",
                 reviews=(Review(malformed=True), _GOOD)),
        Scenario("generation_error", "Every image call fails; the run ends in error.", failing_generations=(1, 2, 3, 4, 5)),
    )
}

AUTO_POOL = ("deliver", "floor_then_deliver")


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


def validation_reply(scenario: Scenario, candidate: int) -> str:
    """The reviewer's JSON reply, as text. The pipeline recomputes pass flags and the decision itself."""
    review = scenario.review_for(candidate)
    if review.malformed:
        return "I compared the two images; the headshot looks good overall."
    names = ("face_match", "attire_accuracy", "background", "realism")
    report: dict[str, Any] = {
        name: {"score": score, "pass": True, "issues": [review.issues[name]] if name in review.issues else []}
        for name, score in zip(names, review.scores)
    }
    report["cultural_sensitivity"] = {"pass": review.cultural, "issues": [] if review.cultural else [review.cultural_issue]}
    report["overall"] = {"pass": True, "action": "deliver"}
    if review.guidance:
        report["overall"]["retry_guidance"] = review.guidance
    return json.dumps(report, indent=2, ensure_ascii=False)


def mock_prompt(user_text: str, template: str) -> str:
    """A deterministic stand-in for the prompt writer: subject notes, then the pipeline's template."""
    try:
        start = user_text.index("```json") + 7
        analysis = json.loads(user_text[start:user_text.index("```", start)])
    except ValueError:
        analysis = {}
    attire, subject = analysis.get("attire", {}), analysis.get("subject", {})
    lines = [
        "Subject specifics for this photo:",
        f"- Headwear: {attire.get('headwear', {}).get('description') or 'none'}",
        f"- Clothing: {attire.get('clothing', {}).get('description') or 'n/a'}",
        f"- Facial hair: {subject.get('facial_hair', {}).get('description') or 'none'}",
        f"- Glasses: {subject.get('glasses', {}).get('frame_description') or 'none'}",
    ]
    if "This is a RETRY." in user_text:
        lines += ["", "Adjusted after reviewer feedback:", user_text.split("feedback:\n", 1)[-1].strip()]
    return "\n".join(lines) + "\n\n" + template


# ── Stand-in clients ──────────────────────────────────────────────────────────

class MockRun(Protocol):
    scenario: Scenario
    profile: str
    attempt: int
    latency: float


# Rough output-token counts for one 1024x1024 image, by quality (GPT Image docs).
_IMAGE_OUTPUT_TOKENS = {"low": 272, "medium": 1056, "high": 4160}


def _text_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _image_tokens(data: bytes) -> int:
    try:
        w, h = image_size(data)
    except Exception:  # noqa: BLE001 - unreadable test bytes still get an estimate
        return 1000
    return min(math.ceil(w * h / 750), 6400)


class MockAnthropic:
    """Answers messages.create like Claude would, for one stage (analyze, prompt or validate)."""

    def __init__(self, run: MockRun, stage: str):
        self.run, self.stage = run, stage
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        if self.run.latency > 0:
            time.sleep(self.run.latency)
        content = kwargs["messages"][0]["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        texts = [b["text"] for b in blocks if b.get("type") == "text"]
        images = [base64.b64decode(b["source"]["data"]) for b in blocks if b.get("type") == "image"]

        if self.stage == "analyze":
            reply = analysis_reply(self.run.profile, self.run.scenario)
        elif self.stage == "prompt":
            from stages.prompt_generator import V3_BASE_TEMPLATE

            reply = mock_prompt(texts[0] if texts else "", V3_BASE_TEMPLATE)
        elif self.stage == "validate":
            reply = validation_reply(self.run.scenario, self.run.attempt)
        else:
            raise ValueError(f"No mock reply for stage {self.stage!r}")

        system = kwargs.get("system") or ""
        usage = NS(
            input_tokens=_text_tokens(system if isinstance(system, str) else json.dumps(system))
            + sum(_text_tokens(t) for t in texts) + sum(_image_tokens(d) for d in images),
            output_tokens=_text_tokens(reply),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        # Like the live model: a thinking block first, then the answer.
        return NS(id=f"mock_{self.stage}", model=kwargs.get("model"), stop_reason="end_turn", usage=usage,
                  content=[NS(type="thinking", thinking="", signature=""), NS(type="text", text=reply)], _request_id=None)


class MockOpenAI:
    """Answers images.edit like GPT Image would, with a locally drawn stand-in image."""

    def __init__(self, run: MockRun):
        self.run = run
        self.images = self

    def edit(self, **kwargs: Any) -> Any:
        if self.run.latency > 0:
            time.sleep(self.run.latency * 2)  # image calls are the slow ones
        model = kwargs.get("model") or ""
        # Like the live API for the gpt-image-2.5 models (FINDINGS.md): input_fidelity is
        # rejected, and stages/image_generator.py retries without it.
        if "input_fidelity" in kwargs and not model.startswith("gpt-image-1"):
            raise RuntimeError(f"(mock) Unknown parameter: 'input_fidelity' is not supported by {model}.")
        candidate = self.run.attempt
        if candidate in self.run.scenario.failing_generations:
            raise RuntimeError("(mock) The image service is unavailable (scripted failure).")

        image = kwargs["image"]
        source = image.read() if hasattr(image, "read") else image[1]
        png = render_mock_headshot(source, attempt=candidate, label=self.run.scenario.name)
        prompt = kwargs.get("prompt") or ""
        usage = NS(
            input_tokens_details=NS(text_tokens=_text_tokens(prompt), image_tokens=_image_tokens(source)),
            output_tokens=_IMAGE_OUTPUT_TOKENS.get(kwargs.get("quality") or "medium", 1056),
        )
        return NS(data=[NS(b64_json=base64.b64encode(png).decode("ascii"))], usage=usage, created=int(time.time()),
                  size=kwargs.get("size"), quality=kwargs.get("quality"), output_format="png", _request_id=None)
