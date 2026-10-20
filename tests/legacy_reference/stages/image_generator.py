"""
Stage 3: Image Generator
Sends the tailored prompt + source photo to OpenAI's GPT Image API
for face-preserving professional headshot generation.

Uses the images.edit() endpoint — the source photo is passed as the
reference image so the model preserves facial features, and the prompt
describes the professional transformation to apply.
"""

import os
import base64

from openai import OpenAI

from config import (
    OPENAI_API_KEY,
    OPENAI_IMAGE_MODEL,
    GENERATION_PARAMS,
    OUTPUT_DIR,
)


class ImageGenerator:
    """Generates a professional headshot using OpenAI's GPT Image API."""

    def __init__(self, output_dir: str | None = None):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.output_dir = output_dir or OUTPUT_DIR

    def generate(
        self,
        prompt: str,
        face_image_path: str,
        output_filename: str = "headshot.png",
        style_reference_paths: list[str] | None = None,
        param_overrides: dict | None = None,
    ) -> str:
        """
        Generate a professional headshot.

        Args:
            prompt: The tailored prompt from Stage 2.
            face_image_path: Path to the original uploaded photo (face reference).
            output_filename: Name for the output file.
            style_reference_paths: Optional paths to studio reference images.
                                   (Currently unused — reserved for future multi-image support.)
            param_overrides: Optional dict to override default generation params
                             (e.g., {"quality": "high"} for a retry).

        Returns:
            Path to the generated headshot image.
        """
        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, output_filename)

        # Merge default params with any overrides
        params = {**GENERATION_PARAMS}
        if param_overrides:
            params.update(param_overrides)

        print(f"  [Stage 3] Sending to OpenAI ({OPENAI_IMAGE_MODEL})...")
        print(f"  [Stage 3] Params: quality={params['quality']}, "
              f"size={params['size']}, "
              f"fidelity={params.get('input_fidelity', 'high')}")

        try:
            # Open the source photo and send it to the edit endpoint.
            # images.edit() preserves the source image's content (face, pose)
            # while applying the transformations described in the prompt.
            with open(face_image_path, "rb") as img_file:
                result = self.client.images.edit(
                    model=OPENAI_IMAGE_MODEL,
                    image=img_file,
                    prompt=prompt,
                    size=params["size"],
                    quality=params["quality"],
                    n=params.get("n", 1),
                )

            # Decode the base64 response and save to disk
            image_base64 = result.data[0].b64_json
            image_bytes = base64.b64decode(image_base64)

            with open(output_path, "wb") as f:
                f.write(image_bytes)

            print(f"  [Stage 3] Generated image saved to: {output_path}")
            return output_path

        except Exception as e:
            raise RuntimeError(f"Image generation failed: {e}")


class MockImageGenerator:
    """
    A mock generator for testing the pipeline without API calls.
    Copies the input image as the 'generated' output so the rest of
    the pipeline can be tested end-to-end.
    """

    def __init__(self, output_dir: str | None = None):
        self.output_dir = output_dir or OUTPUT_DIR

    def generate(
        self,
        prompt: str,
        face_image_path: str,
        output_filename: str = "headshot.png",
        style_reference_paths: list[str] | None = None,
        param_overrides: dict | None = None,
    ) -> str:
        import shutil

        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, output_filename)

        # Simply copy the input as "generated" for pipeline testing
        shutil.copy2(face_image_path, output_path)
        print(f"  [Stage 3 MOCK] Copied input to: {output_path}")
        print(f"  [Stage 3 MOCK] Prompt length: {len(prompt)} chars")

        return output_path


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m stages.image_generator <prompt_file> <face_image>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        prompt = f.read()

    generator = ImageGenerator()
    result = generator.generate(prompt, sys.argv[2])
    print(f"Output: {result}")
