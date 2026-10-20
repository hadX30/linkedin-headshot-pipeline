"""
Stage 2: Prompt Generator
Takes the analyzer's JSON output and writes a tailored generation prompt
based on the v3 unified prompt template.
"""

import json
import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

# ── The v3 unified prompt (edit-framed, filter-safe) ──────────────────────────
V3_BASE_TEMPLATE = r"""Photo editing task — professional studio portrait retouch for LinkedIn.

Edit the supplied photograph into a polished LinkedIn headshot. This is a photo-editing and retouching task, not a reconstruction. The person in the output is the same person in the input — you are changing the photographic environment (background, lighting, crop) and, where specified, the wardrobe. You are retouching an existing photo, the way a professional photographer would in post-production.

This prompt is calibrated against a set of reference shots from this studio. Match the lighting quality, background tone, color grading, and framing of those references.

--- PRESERVE THE PERSON AS THEY APPEAR ---

1. Face: Keep every facial feature exactly as the input photo — bone structure, nose shape, lip shape, jawline, chin, cheekbones, eye color, eye shape, eyebrow thickness and arch. The face in the output is the face from the input, untouched.

2. Skin: Keep the exact skin tone, texture, and all natural details — pores, wrinkles, lines, scars, birthmarks, moles, freckles — in their original positions and sizes. Maintain the skin's authentic character. The lighting changes around the person, the skin itself stays true to the source.

3. Facial hair: Keep all facial hair exactly as it appears — beard shape, mustache, density, length, any gray hairs, any patchiness. Reproduce faithfully from the source.

4. Glasses: If present, keep the exact frame shape, frame color, and lens type. Minimize glare naturally while keeping the glasses fully intact.

5. Jewelry and accessories: Keep all visible jewelry and accessories (rings, earrings, necklace, watch, pen clip) exactly as in the source. If the adjusted crop hides an accessory, simply leave it out rather than guessing what it looks like.

6. Expression: Keep the person's natural expression exactly as it appears in the source photo.

--- PRESERVE TRADITIONAL AND CULTURAL ATTIRE ---

These garments are fixed elements — they are part of the person's identity in the photo. Treat them as you would the face: reproduced faithfully, with full accuracy.

7. Niqab: If the person is wearing a niqab, keep the entire garment exactly as it appears — fabric, draping, color, and coverage are all fixed. The output reproduces only what is visible in the source. Everything else remains as the source shows it.

8. Hijab: Keep its exact color, fabric, texture, draping style, and pin placement. The coverage in the output matches the coverage in the source identically.

9. Shemagh (ghutra): Keep the exact pattern from the source — red-and-white houndstooth, pure white, black-and-white, or whichever pattern appears. Reproduce the weave faithfully. If an iqal (black cord) is present, include it. The shemagh looks identical to the source even with a framing adjustment.

10. Thobe: If present, keep the exact color, collar style, and fabric appearance.

--- WARDROBE EDITING (ONLY WHEN CASUAL CLOTHING IS PRESENT) ---

When the person is wearing casual, non-professional clothing, edit the wardrobe to match the studio's professional standard. If the person is already wearing professional or traditional attire, keep it as-is.

11. Women's wardrobe edits:
    - Hijab with casual clothing: Keep the hijab exactly. Edit the casual clothing into a black abaya — realistic heavy fabric with natural draping, matte finish, and visible stitching details.
    - Without hijab, casual clothing: Edit into a black professional suit — wool or crepe texture, visible lapel stitching, natural creases at the elbow.
    - Niqab: Keep everything as the source. The niqab outfit is the final outfit.
    - Already wearing professional attire (suit, blazer, abaya): Keep as-is.

12. Men's wardrobe edits:
    - Shemagh with casual clothing: Keep the shemagh exactly. Edit the casual clothing into a white thobe — realistic cotton or light polyester, visible collar button, natural draping, tailored fit.
    - Casual clothing without shemagh: Edit in a white thobe with a white shemagh and iqal. Use a photorealistic, well-tailored Saudi-style look with natural fabric weight.
    - Already wearing a suit or thobe: Keep as-is.

--- STUDIO ENVIRONMENT EDITS (FROM REFERENCE SET) ---

These are the photographic-environment changes applied to the source photo. The person stays the same; the studio setting around them changes.

13. Background edit:
    - Replace the existing background with a warm light gray (#E8E6E3) seamless studio backdrop.
    - Apply a subtle luminance gradient: slightly brighter directly behind the subject's head and upper shoulders, easing to a slightly deeper gray toward the frame edges. This is natural light falloff on a studio backdrop.

14. Pose and framing edit (adjust from the source, keeping the person natural):
    - Primary — Arms crossed: Arms folded at chest level, relaxed and slightly asymmetric. Shoulders back, relaxed. Natural body angle. If a pen is clipped to the thobe pocket in the source, keep it.
    - Secondary — Standing relaxed: Arms at sides or one hand lightly at waist. Shoulders turned 15-20 degrees from camera. Confident, approachable body language.
    - For women in hijab or abaya: Natural three-quarter body turn (20-30 degrees), shoulders angled to camera, hands relaxed at sides or lightly clasped in front.

15. Face angle edit:
    - Adjust to a three-quarter view (approximately 30-45 degrees).
    - Face turned slightly toward the key light side.
    - Chin level or very slightly downward (2-3 degrees).
    - Eyes directed at the camera lens. Both eyes visible, the nearer eye in sharpest focus.

16. Crop:
    - Mid-torso to chest level — wider for arms-crossed to include the full gesture, tighter for relaxed standing.
    - Face positioned in the upper third of the frame.
    - 8-12% breathing room above the head (or above the shemagh and iqal).
    - 1:1 square aspect ratio.

17. Lighting edit (applied to the scene, matched to studio references):
    - Key light: From camera-right at approximately 45 degrees, large softbox. Warm color temperature around 5200K. Soft, graduated shadow transition on the face — both sides well-lit.
    - Fill light: From camera-left, approximately 1 stop below key. Open, approachable look with gentle shadow modeling.
    - Rim/separation light: Subtle edge light from behind and above. Gentle separation between the subject and background.
    - Catch lights: Two small rectangular catch lights in the eyes.
    - The lighting edits change how light falls on the scene. The person's actual skin tone remains a faithful match to the source photo.

18. Photographic quality:
    - Skin: Visible pores on cheeks and nose. Natural under-eye texture. Real subtle shine where natural. Lips with real texture.
    - Fabric: Thobe shows visible cotton or polyester weave. Shemagh shows individual threads of its pattern. Abaya shows the weight of real fabric with natural creases.
    - Depth of field: Shallow, f/2.8 to f/4 equivalent. Sharpest focus on the near eye. Background uniformly soft.
    - Optical character: Slight natural lens vignette. Clean image — the quality of a Canon 5D IV or Sony A7R IV with an 85mm portrait lens.
    - Color grading: Neutral to slightly warm. Clean midtones. Balanced shadows and highlights.
    - Overall: The result should look like a professionally retouched version of the supplied photograph, taken in a real studio.

--- OUTPUT ---

19. Resolution: 1024x1024 minimum (2048x2048 preferred).
20. Color space: sRGB, with embedded ICC profile.
21. Format: PNG for studio delivery, JPEG (95% quality) for client preview.
22. Strip location metadata. Retain color profile."""


# ── System prompt for the prompt generator ────────────────────────────────────
GENERATOR_SYSTEM_PROMPT = f"""You are a prompt engineer for a portrait photography pipeline.
You will receive a JSON analysis of a person's photo.
Your job is to write a generation prompt tailored to this specific photo.

Use the base template below as your foundation, but customize it:

- If the person is already in professional attire, remove all wardrobe instructions.
- If the person wears a niqab, remove all wardrobe and pose instructions. Keep only background and lighting edits.
- Describe the shemagh pattern using the analyzer's exact words (e.g., "red and white houndstooth with zigzag border"), not generic terms.
- If the analyzer flagged beauty filters, add a note: "Prioritize the underlying facial structure visible through the filter."
- If the mode is "reduced_edit", write a minimal prompt: background replacement, lighting adjustment, and crop only. Keep pose, clothing, and all features exactly as the source.
- Reference the person's specific accessories by name (e.g., "silver round glasses", "black leather watch on left wrist").
- Describe the person's facial hair precisely (e.g., "short trimmed full beard, dark brown, slight gray at chin").
- If glasses are present, describe them specifically (e.g., "silver round prescription glasses with thin metal frames").

Your output is the prompt text only. No explanation, no JSON wrapping.
Write it in the same calm, descriptive, edit-framing style as the base template.

--- BASE TEMPLATE ---
{V3_BASE_TEMPLATE}"""


class PromptGenerator:
    """Generates a tailored image-generation prompt from analyzer JSON."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def generate(
        self, analysis: dict, retry_guidance: str | None = None
    ) -> str:
        """
        Generate a tailored prompt from the analyzer's JSON.

        Args:
            analysis: The structured JSON from Stage 1 (PhotoAnalyzer).
            retry_guidance: Optional feedback from the validator on a retry
                            (e.g., "Increase face_id_scale. Add negative prompt: hazel eyes.").

        Returns:
            A tailored prompt string ready for the image generation model.
        """
        user_message = f"Here is the photo analysis:\n\n```json\n{json.dumps(analysis, indent=2, ensure_ascii=False)}\n```"

        if retry_guidance:
            user_message += (
                f"\n\nThis is a RETRY. The previous generation failed quality validation. "
                f"Adjust the prompt based on this feedback:\n{retry_guidance}"
            )

        message = self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            system=GENERATOR_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": user_message,
                }
            ],
        )

        prompt_text = message.content[0].text.strip()

        # Remove any markdown fences the model might wrap the output in
        if prompt_text.startswith("```"):
            lines = prompt_text.split("\n")
            # Strip first line and last line if they are fences
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            prompt_text = "\n".join(lines)

        return prompt_text


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m stages.prompt_generator <analysis_json_file>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        analysis = json.load(f)

    generator = PromptGenerator()
    prompt = generator.generate(analysis)
    print(prompt)
