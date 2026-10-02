"""
Stage 3: Image Generator
Sends the tailored prompt + face reference photo to a face-preserving
image generation model via the Replicate API.
"""

import os
import base64
import time
import httpx
import replicate

from config import (
    REPLICATE_API_TOKEN,
    GENERATION_PARAMS,
    REFERENCE_IMAGES,
    OUTPUT_DIR,
)


class ImageGenerator:
    """Generates a professional headshot using a face-preserving model on Replicate."""

    def __init__(self):
        os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN
        self.client = replicate.Client(api_token=REPLICATE_API_TOKEN)

    def _image_to_data_uri(self, image_path: str) -> str:
        """Convert an image file to a data URI for the Replicate API."""
        ext = image_path.lower().rsplit(".", 1)[-1]
        mime_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
        }
        mime = mime_map.get(ext, "image/jpeg")

        with open(image_path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")

        return f"data:{mime};base64,{data}"

    def _download_image(self, url: str, output_path: str) -> str:
        """Download an image from a URL and save to disk."""
        with httpx.Client(timeout=120) as client:
            response = client.get(url)
            response.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(response.content)
        return output_path

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
            param_overrides: Optional dict to override default generation params
                             (e.g., {"face_id_scale": 0.9} on retry).

        Returns:
            Path to the generated headshot image.
        """
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(OUTPUT_DIR, output_filename)

        # Merge default params with any overrides
        params = {**GENERATION_PARAMS}
        if param_overrides:
            params.update(param_overrides)

        # Prepare the face reference image
        face_uri = self._image_to_data_uri(face_image_path)

        # Use style references if provided, otherwise fall back to configured ones
        style_refs = style_reference_paths or REFERENCE_IMAGES
        style_uris = [self._image_to_data_uri(p) for p in style_refs[:3]]

        # ── Call the Replicate API ────────────────────────────────────────
        # The exact input schema depends on which model is deployed.
        # This is structured for InstantID on Replicate — the most common
        # face-preserving model available there.
        #
        # When switching models (e.g., to Flux + PuLID or fal.ai),
        # only this method needs to change.

        input_payload = {
            "prompt": prompt,
            "negative_prompt": params["negative_prompt"],
            "image": face_uri,               # face reference
            "width": params["width"],
            "height": params["height"],
            "guidance_scale": params["guidance_scale"],
            "num_inference_steps": params["num_inference_steps"],
            "ip_adapter_scale": params["ip_adapter_scale"],
            "controlnet_conditioning_scale": params["controlnet_conditioning_scale"],
        }

        # Add seed if not random
        if params.get("seed", -1) != -1:
            input_payload["seed"] = params["seed"]

        # Add style reference if available
        if style_uris:
            # Some models accept a second image input for style reference
            input_payload["pose_image"] = style_uris[0]

        print(f"  [Stage 3] Sending to Replicate (InstantID)...")
        print(f"  [Stage 3] Params: face_id={params['face_id_scale']}, "
              f"ip_adapter={params['ip_adapter_scale']}, "
              f"guidance={params['guidance_scale']}, "
              f"steps={params['num_inference_steps']}")

        try:
            # Run the model
            # Using the generic replicate.run() which handles polling
            output = self.client.run(
                "zsxkib/instant-id:a]2b5bdc0e1b7b20ebbcb3a2aff1deb0d0a1b3b4c5d6e7f8a9b0c1d2e3f4a5b6",
                input=input_payload,
            )

            # Output is typically a URL or list of URLs
            if isinstance(output, list):
                image_url = str(output[0])
            elif hasattr(output, "url"):
                image_url = output.url
            else:
                image_url = str(output)

            # Download the generated image
            self._download_image(image_url, output_path)
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

    def generate(
        self,
        prompt: str,
        face_image_path: str,
        output_filename: str = "headshot.png",
        style_reference_paths: list[str] | None = None,
        param_overrides: dict | None = None,
    ) -> str:
        import shutil

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(OUTPUT_DIR, output_filename)

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
