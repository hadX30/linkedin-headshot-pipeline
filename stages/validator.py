"""
Stage 4: Quality Validator (original import path).
Sends both the original photo and the generated headshot to Claude Vision
for a side-by-side quality check. Returns structured QA JSON.
"""

import json
import anthropic

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    VALIDATION_THRESHOLDS,
    HARD_FLOORS,
    COMPOSITE_WEIGHTS,
    COMPOSITE_DELIVER_THRESHOLD,
)

from ._response import extract_text

NUMERIC_CHECKS = ["face_match", "attire_accuracy", "background", "realism"]

VALIDATOR_SYSTEM_PROMPT = """You are a quality control reviewer for a professional portrait studio.
You will receive two images: the ORIGINAL photo and the GENERATED headshot.
Compare them carefully and return a JSON quality report.

{
  "face_match": {
    "score": 0-100,
    "pass": true/false,
    "issues": ["eye color shifted from brown to hazel"]
  },
  "attire_accuracy": {
    "score": 0-100,
    "pass": true/false,
    "issues": ["shemagh pattern changed from houndstooth to plain"]
  },
  "background": {
    "score": 0-100,
    "pass": true/false,
    "issues": []
  },
  "realism": {
    "score": 0-100,
    "pass": true/false,
    "issues": ["skin appears overly smooth on forehead"]
  },
  "cultural_sensitivity": {
    "pass": true/false,
    "issues": ["hijab coverage changed — hair visible at temple"]
  },
  "overall": {
    "pass": true/false,
    "action": "deliver" | "retry" | "manual_review",
    "retry_guidance": "Increase face_id_scale to 0.9. Add negative prompt: hazel eyes."
  }
}

Scoring guidelines:
- face_match (threshold: 85): The generated face must be the SAME person. Check: bone structure, nose shape, lip shape, jawline, eye color, eye shape, eyebrow arch, facial hair (exact match in shape, density, color, gray pattern), glasses (exact frame match), skin tone, birthmarks/moles/scars, expression. Any drift in any of these is a deduction.

- attire_accuracy (threshold: 90): Cultural and traditional garments must be pixel-accurate reproductions. Check: shemagh pattern matches exactly (not a similar pattern — THE SAME pattern), hijab color/draping/coverage unchanged, niqab fully preserved, thobe color/collar unchanged. For wardrobe changes (casual → professional), check that the new attire looks realistic (visible fabric texture, natural draping, no AI sheen).

- background (threshold: 80): A clean, uniform, professional studio backdrop in a neutral gray tone. Judge this the way a photo editor judges a delivered studio shot, NOT as a pixel-diff against a reference swatch. Score against these criteria in descending order of importance:
    1. Is the original environment fully removed? Nothing from the source scene — café, office, street, furniture, other people — bleeding through at the frame edges or through gaps between the arms and torso. This is the dominant factor.
    2. Is the backdrop free of visible rendering artifacts: banding, smearing, repeated texture, or a halo tracing the subject's outline?
    3. Is the tone a neutral gray? Anywhere from light warm gray through mid gray is correct.
    4. Is there natural light falloff across the backdrop rather than a flat digital fill?
  Score 90+ when all four hold. Score 80-89 when the environment is fully replaced and the backdrop is clean, with only minor tonal deviation or slightly flat lighting. Score 70-79 for a clean replacement with a visible flaw such as mild banding. Score below 70 ONLY when the original environment is still partially visible or there are obvious rendering artifacts.
  Do NOT deduct because the gray differs from a specific target hex value. Any clean, professional, neutral backdrop is correct. A tonal preference is not a defect.

- realism (threshold: 80): The output should look like a real photograph, not AI-generated. Check: visible skin pores, natural under-eye texture, realistic fabric weave, natural shadows under chin/nose, no plastic/waxy skin, no over-sharpening, natural catch lights in eyes, correct depth of field (sharp on eyes, soft background).

- cultural_sensitivity (HARD GATE — pass/fail only): Any change to hijab coverage, niqab coverage, shemagh pattern, or thobe is an automatic fail regardless of other scores. This is the most important check.

FACE COVERAGE ADJUSTMENT — applies to face_match only

Activated only when the user message states that the face is substantially covered.

Score face_match on the visible region as a complete evaluation: eye shape and set, iris colour, eyelid fold, lash detail, eyeliner or makeup styling, the spacing between the eyes, and the tone and texture of the surrounding skin. A visible region that closely matches the original deserves 90 or above. Do not reduce face_match because the nose, mouth or jaw cannot be compared — those regions are absent from both images, so they are not evidence of a worse result.

This adjustment changes face_match and nothing else. Every other check, cultural_sensitivity included, is judged exactly as specified above, with no allowance.

Decision logic:
- "deliver": ALL checks pass their thresholds AND cultural_sensitivity passes.
- "retry": 1-2 checks fail with fixable issues AND cultural_sensitivity passes. Provide specific retry_guidance.
- "manual_review": cultural_sensitivity fails, OR 3+ checks fail, OR issues seem unfixable via prompt adjustment.

Note: your "action" is advisory. The pipeline generates several candidates, keeps all of them, and makes the final delivery decision itself using hard floors and a weighted composite. Your job is to score each image accurately and independently. Score what you actually see — do not inflate a score because you assume it is the last chance, and do not deflate one because you assume another attempt will follow.

Scoring discipline:
- Score each image on its own merits against the rubric above.
- Reserve sub-70 scores for real defects, not for mild deviation from an ideal.
- Do not let a weakness in one dimension drag down your scores in the others. A poor background does not make the face match worse.

For retry_guidance, be specific and actionable. The generator is an image-editing model driven ENTIRELY by the prompt text — there are no numeric knobs like face strength or negative prompts available. So phrase guidance as prompt-wording changes:
- What the next prompt should state more explicitly (e.g., "State that the eyeglass frames are thin silver round wire, not thick black")
- What wording is causing drift and should be removed or softened
- Which preservation instruction needs to be more emphatic (e.g., "Re-state the beard's gray pattern in the preserve section")
Never suggest adjusting a numeric generation parameter — none are available.

Return ONLY the JSON object. No explanation, no markdown fences."""


class QualityValidator:
    """Validates a generated headshot against the original photo."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def _load_image_as_base64(self, image_path: str) -> tuple[str, str]:
        """Load an image file and return (base64_data, media_type)."""
        ext = image_path.lower().rsplit(".", 1)[-1]
        media_type_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        media_type = media_type_map.get(ext, "image/jpeg")

        with open(image_path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")

        return data, media_type

    @staticmethod
    def face_is_covered(analysis: dict | None) -> bool:
        """
        True when the analyzer reports attire that hides most of the face.

        Used to switch face_match to an eye-region evaluation. This is a narrow,
        objective fact taken from Stage 1 -- "is there a niqab in this photo" --
        not a quality judgement, so it does not compromise the validator's
        independent assessment of the images.
        """
        if not analysis:
            return False
        headwear = (analysis.get("attire") or {}).get("headwear") or {}
        kind = str(headwear.get("type") or "").strip().lower()
        if kind in {"niqab", "burqa", "burka", "face_veil", "face veil"}:
            return True
        # Fall back to the free-text description for wordings the type misses.
        text = str(headwear.get("description") or "").lower()
        return any(w in text for w in ("niqab", "burqa", "face veil", "face-veil"))

    def validate(
        self,
        original_path: str,
        generated_path: str,
        analysis: dict | None = None,
    ) -> dict:
        """
        Compare the original photo against the generated headshot.

        Args:
            original_path: Path to the original uploaded photo.
            generated_path: Path to the generated headshot.
            analysis: Optional Stage 1 output. When it reports a niqab or
                      similar face covering, face_match is scored on the
                      visible eye region as a complete evaluation rather than
                      as a fraction of a whole face. Measured across three live
                      runs, the covered case scored 80 against 84 and 88 for
                      open faces -- the depressed score reflected missing
                      evidence, not a worse result, and sat one point above the
                      78 hard floor.

        Returns:
            dict with QA report including face_match, attire_accuracy,
            background, realism, cultural_sensitivity, and overall verdict.
        """
        orig_data, orig_mime = self._load_image_as_base64(original_path)
        gen_data, gen_mime = self._load_image_as_base64(generated_path)

        covered = self.face_is_covered(analysis)
        if covered:
            print("  [Stage 4] Face covering detected — scoring face_match on the eye region")

        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            # NOTE ON SCORING REPEATABILITY
            # This validator was observed giving background scores of 40, 72 and
            # 40 to three byte-identical images -- 32 points of spread on the
            # same input. That noise feeds the composite, and because the
            # pipeline selects the highest-scoring candidate it means selection
            # partly rewards a lucky scoring draw rather than a better image.
            #
            # The usual fix, temperature=0, is NOT available: models with
            # extended thinking run at a fixed temperature, and the parameter
            # was removed from messages.create() in anthropic SDK 1.x.
            # Variance must therefore be measured rather than suppressed --
            # see tools/measure_validator_variance.py.
            system=VALIDATOR_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "ORIGINAL photo (the input the client uploaded):",
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": orig_mime,
                                "data": orig_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": "GENERATED headshot (the pipeline output to validate):",
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": gen_mime,
                                "data": gen_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Compare these two images and return the structured JSON quality report."
                                + (
                                    "\n\nNOTE: This subject wears a niqab or similar face covering, so "
                                    "only the eye region is visible. Apply the FACE COVERAGE ADJUSTMENT "
                                    "to face_match ONLY. Judge cultural_sensitivity and attire_accuracy "
                                    "with full strictness — the covering itself must be compared closely "
                                    "and any reduction in coverage is a cultural failure."
                                    if covered else ""
                                )
                            ),
                        },
                    ],
                }
            ],
        )

        raw_text = extract_text(message).strip()

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.strip() == "```" and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            raw_text = "\n".join(json_lines)

        try:
            qa_report = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Failed to parse validator response as JSON: {e}\nRaw response:\n{raw_text}"
            )

        # Enforce thresholds from config (in case the model used different ones)
        self._enforce_thresholds(qa_report)

        return qa_report

    def _enforce_thresholds(self, report: dict) -> None:
        """
        Re-apply our configured thresholds and compute the delivery verdict.

        Two layers:
          1. Hard floors (HARD_FLOORS) plus the cultural gate. These are
             correctness gates — a candidate failing one is wrong, not merely
             imperfect, and no composite score can compensate.
          2. A weighted composite (COMPOSITE_WEIGHTS) across the numeric checks.

        A candidate is deliverable when it clears every hard floor, passes the
        cultural gate, and its composite reaches COMPOSITE_DELIVER_THRESHOLD.

        The per-check `pass` flags are still computed against the ideal
        thresholds and reported, because the studio team uses them to see where
        an image was weak — but they no longer gate delivery on their own.
        """
        # Per-check pass flags against ideal thresholds (reporting only).
        for check_name, threshold in VALIDATION_THRESHOLDS.items():
            if check_name in report and "score" in report[check_name]:
                report[check_name]["pass"] = report[check_name]["score"] >= threshold

        checks_failed = [
            name
            for name in NUMERIC_CHECKS
            if name in report and not report[name].get("pass", True)
        ]

        cultural_pass = report.get("cultural_sensitivity", {}).get("pass", True)

        # ── Layer 1: hard floors ──────────────────────────────────────────
        floors_failed = []
        for check_name, floor in HARD_FLOORS.items():
            score = report.get(check_name, {}).get("score")
            if score is None or score < floor:
                floors_failed.append(f"{check_name} ({score} < {floor})")

        floors_pass = cultural_pass and not floors_failed

        # ── Layer 2: weighted composite ───────────────────────────────────
        # Round once, here, and compare against the rounded value. If the raw
        # score were compared instead, a candidate at 81.95 would be reported
        # as "composite 82.0" against a bar of 82 yet still be rejected, which
        # reads as a bug to anyone inspecting the QA record.
        composite = round(self.compute_composite(report), 1)

        # ── Verdict ───────────────────────────────────────────────────────
        model_guidance = report.get("overall", {}).get("retry_guidance")

        if not cultural_pass:
            action = "manual_review"
            guidance = model_guidance or (
                "Cultural sensitivity check failed. Route to manual studio processing."
            )
        elif floors_failed:
            # A correctness gate was breached. Another candidate may clear it,
            # so this is a retry rather than an immediate manual review; the
            # pipeline escalates if no candidate clears the floors.
            action = "retry"
            guidance = model_guidance or (
                f"Hard floor not met: {', '.join(floors_failed)}."
            )
        elif composite >= COMPOSITE_DELIVER_THRESHOLD:
            action = "deliver"
            guidance = None
        else:
            action = "retry"
            guidance = model_guidance or (
                f"Composite {composite:.1f} below delivery bar "
                f"{COMPOSITE_DELIVER_THRESHOLD}. Weakest checks: "
                f"{', '.join(checks_failed) if checks_failed else 'none individually'}."
            )

        report["overall"] = {
            "pass": action == "deliver",
            "action": action,
            "composite": composite,
            "floors_pass": floors_pass,
            "floors_failed": floors_failed,
            "checks_failed": checks_failed,
            "retry_guidance": guidance,
        }

    @staticmethod
    def compute_composite(report: dict) -> float:
        """
        Weighted composite across the numeric checks.

        Weights are renormalized over whichever checks are actually present, so
        a malformed report missing one check still produces a comparable score
        rather than silently scoring low.
        """
        total_weight = 0.0
        weighted_sum = 0.0

        for check_name, weight in COMPOSITE_WEIGHTS.items():
            score = report.get(check_name, {}).get("score")
            if isinstance(score, (int, float)):
                weighted_sum += score * weight
                total_weight += weight

        if total_weight == 0:
            return 0.0

        return weighted_sum / total_weight


def select_best_candidate(candidates: list[dict]) -> dict | None:
    """
    Pick the winning candidate from a set of generated attempts.

    This is what replaces the old sequential retry behaviour. Previously the
    pipeline discarded each attempt the moment it failed and regenerated from
    scratch, so a strong early image could be thrown away and replaced by a
    weaker later one. Here every candidate is kept and compared.

    Args:
        candidates: list of dicts, each with at least:
            "attempt": int
            "path": str — path to the generated image
            "validation": dict — the QA report from QualityValidator

    Returns:
        The winning candidate dict, annotated with "deliverable" (bool), or
        None if the list is empty.

    Selection order:
        1. Candidates clearing all hard floors and the cultural gate, ranked by
           composite — the highest wins.
        2. If none clear the floors, the highest composite overall is returned
           with deliverable=False, so the studio team reviewing the job still
           receives the best available image rather than the last one produced.
    """
    if not candidates:
        return None

    def composite_of(candidate: dict) -> float:
        return candidate.get("validation", {}).get("overall", {}).get("composite", 0.0)

    def is_deliverable(candidate: dict) -> bool:
        overall = candidate.get("validation", {}).get("overall", {})
        return (
            overall.get("floors_pass", False)
            and overall.get("composite", 0.0) >= COMPOSITE_DELIVER_THRESHOLD
        )

    deliverable = [c for c in candidates if is_deliverable(c)]

    if deliverable:
        winner = max(deliverable, key=composite_of)
        winner["deliverable"] = True
        return winner

    winner = max(candidates, key=composite_of)
    winner["deliverable"] = False
    return winner

from headshot.compat import QualityValidator
from headshot.prompts import VALIDATOR_SYSTEM_PROMPT  # noqa: F401

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m stages.validator <original_image> <generated_image>")
        sys.exit(1)

    validator = QualityValidator()
    result = validator.validate(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2, ensure_ascii=False))
