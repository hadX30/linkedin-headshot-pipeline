"""
Stage 1: Photo Analyzer
Sends the uploaded photo to Claude Vision and extracts structured JSON
describing the subject, attire, photo quality, and pipeline action.
"""

import base64
import json
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

ANALYZER_SYSTEM_PROMPT = """You are a portrait photography assistant. Analyze the uploaded photo and return a JSON object with the following fields. Be precise and factual — describe only what you see.

{
  "subject": {
    "apparent_gender": "male" | "female",
    "apparent_age_range": "child" | "teen" | "young_adult" | "adult" | "senior",
    "skin_tone": "description in Fitzpatrick scale or natural terms",
    "facial_hair": {
      "present": true/false,
      "type": "full_beard" | "goatee" | "mustache" | "stubble" | "clean_shaven",
      "description": "short and well-trimmed, dark brown with some gray"
    },
    "expression": "smiling" | "neutral" | "slight_smile" | "serious",
    "glasses": {
      "present": true/false,
      "type": "prescription" | "sunglasses" | "reading",
      "frame_description": "silver round frames, thin metal"
    },
    "visible_accessories": ["black watch on left wrist", "pen clipped to chest pocket"]
  },
  "attire": {
    "headwear": {
      "type": "none" | "hijab" | "niqab" | "shemagh_with_iqal" | "shemagh_without_iqal" | "other",
      "color": "red and white houndstooth",
      "description": "traditional Saudi shemagh, red/white pattern, black iqal"
    },
    "clothing": {
      "type": "thobe" | "suit" | "abaya" | "casual" | "uniform" | "other",
      "is_professional": true/false,
      "color": "white",
      "description": "white thobe with standard collar, pen in chest pocket"
    },
    "needs_wardrobe_change": true/false,
    "recommended_outfit": "Keep current thobe and shemagh as-is"
  },
  "photo_quality": {
    "resolution_sufficient": true/false,
    "face_visible": true/false,
    "face_angle": "frontal" | "three_quarter" | "profile" | "extreme",
    "lighting_quality": "studio" | "natural_good" | "natural_poor" | "harsh" | "backlit",
    "has_beauty_filters": true/false,
    "multiple_faces": true/false,
    "background_type": "solid" | "indoor" | "outdoor" | "studio"
  },
  "pipeline_action": {
    "proceed": true/false,
    "mode": "full_edit" | "reduced_edit" | "reject",
    "reject_reason": null | "multiple_faces" | "no_face" | "low_resolution" | "minor_detected",
    "notes": "Subject already in professional attire with shemagh. Only background, lighting, and crop adjustments needed."
  }
}

Important rules for your analysis:
- If the person appears to be a child or teenager (under 18), set apparent_age_range accordingly and set mode to "reduced_edit".
- If multiple faces are detected, set proceed to false and mode to "reject" with reject_reason "multiple_faces".
- If no face is clearly visible, set proceed to false and mode to "reject" with reject_reason "no_face".
- needs_wardrobe_change should be false if the person is already wearing professional or traditional attire (thobe, suit, abaya, professional blazer).
- For shemagh, describe the exact pattern you see — houndstooth, plain white, checkered — using precise color terms.
- For hijab, note the exact color, fabric appearance, and how it's draped/pinned.
- Return ONLY the JSON object. No explanation, no markdown fences, no other text."""


class PhotoAnalyzer:
    """Analyzes an uploaded photo using Claude Vision."""

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

    def analyze(self, image_path: str) -> dict:
        """
        Analyze a photo and return structured JSON.

        Args:
            image_path: Path to the uploaded photo (JPEG or PNG).

        Returns:
            dict with keys: subject, attire, photo_quality, pipeline_action
        """
        image_data, media_type = self._load_image_as_base64(image_path)

        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=ANALYZER_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Analyze this portrait photo and return the structured JSON analysis.",
                        },
                    ],
                }
            ],
        )

        # Extract the text response
        raw_text = message.content[0].text.strip()

        # Parse JSON — handle cases where the model wraps in markdown fences
        if raw_text.startswith("```"):
            # Strip markdown code fences
            lines = raw_text.split("\n")
            # Remove first and last lines (``` markers)
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
            analysis = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Failed to parse analyzer response as JSON: {e}\nRaw response:\n{raw_text}"
            )

        return analysis


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m stages.analyzer <image_path>")
        sys.exit(1)

    analyzer = PhotoAnalyzer()
    result = analyzer.analyze(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
