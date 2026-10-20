"""
Stage 4: Quality Validator
Sends both the original photo and the generated headshot to Claude Vision
for a side-by-side quality check. Returns structured QA JSON.
"""

import base64
import json
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, VALIDATION_THRESHOLDS

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

- background (threshold: 95): Should be a warm light gray (#E8E6E3) seamless studio backdrop with subtle luminance gradient (brighter behind head, slightly darker at edges). Check: correct color tone (warm gray, not cool gray or white), no artifacts, no remnants of original background bleeding through.

- realism (threshold: 80): The output should look like a real photograph, not AI-generated. Check: visible skin pores, natural under-eye texture, realistic fabric weave, natural shadows under chin/nose, no plastic/waxy skin, no over-sharpening, natural catch lights in eyes, correct depth of field (sharp on eyes, soft background).

- cultural_sensitivity (HARD GATE — pass/fail only): Any change to hijab coverage, niqab coverage, shemagh pattern, or thobe is an automatic fail regardless of other scores. This is the most important check.

Decision logic:
- "deliver": ALL checks pass their thresholds AND cultural_sensitivity passes.
- "retry": 1-2 checks fail with fixable issues AND cultural_sensitivity passes. Provide specific retry_guidance.
- "manual_review": cultural_sensitivity fails, OR 3+ checks fail, OR issues seem unfixable via prompt adjustment.

For retry_guidance, be specific and actionable:
- Name the exact parameter to adjust (e.g., "Increase face_id_scale from 0.85 to 0.9")
- Suggest negative prompt additions (e.g., "Add to negative prompt: hazel eyes, smooth skin")
- Describe what the prompt should emphasize differently

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

    def validate(self, original_path: str, generated_path: str) -> dict:
        """
        Compare the original photo against the generated headshot.

        Args:
            original_path: Path to the original uploaded photo.
            generated_path: Path to the generated headshot.

        Returns:
            dict with QA report including face_match, attire_accuracy,
            background, realism, cultural_sensitivity, and overall verdict.
        """
        orig_data, orig_mime = self._load_image_as_base64(original_path)
        gen_data, gen_mime = self._load_image_as_base64(generated_path)

        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
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
                            "text": "Compare these two images and return the structured JSON quality report.",
                        },
                    ],
                }
            ],
        )

        raw_text = message.content[0].text.strip()

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
        Re-apply our configured thresholds to the validator's scores.
        The model might judge differently, so we override pass/fail
        based on our thresholds.
        """
        for check_name, threshold in VALIDATION_THRESHOLDS.items():
            if check_name in report and "score" in report[check_name]:
                report[check_name]["pass"] = report[check_name]["score"] >= threshold

        # Recalculate overall verdict
        checks_failed = []
        for check_name in ["face_match", "attire_accuracy", "background", "realism"]:
            if check_name in report and not report[check_name].get("pass", True):
                checks_failed.append(check_name)

        cultural_pass = report.get("cultural_sensitivity", {}).get("pass", True)

        if not cultural_pass:
            report["overall"] = {
                "pass": False,
                "action": "manual_review",
                "retry_guidance": report.get("overall", {}).get(
                    "retry_guidance",
                    "Cultural sensitivity check failed. Route to manual studio processing.",
                ),
            }
        elif len(checks_failed) >= 3:
            report["overall"] = {
                "pass": False,
                "action": "manual_review",
                "retry_guidance": report.get("overall", {}).get(
                    "retry_guidance",
                    f"Multiple checks failed ({', '.join(checks_failed)}). Manual review needed.",
                ),
            }
        elif len(checks_failed) > 0:
            report["overall"] = {
                "pass": False,
                "action": "retry",
                "retry_guidance": report.get("overall", {}).get(
                    "retry_guidance",
                    f"Checks failed: {', '.join(checks_failed)}. Adjust generation parameters.",
                ),
            }
        else:
            report["overall"] = {
                "pass": True,
                "action": "deliver",
                "retry_guidance": None,
            }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m stages.validator <original_image> <generated_image>")
        sys.exit(1)

    validator = QualityValidator()
    result = validator.validate(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2, ensure_ascii=False))
