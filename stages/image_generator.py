"""
Stage 3: Image Generator (original import path).
Sends the tailored prompt + source photo to the image edit API.
"""

from headshot.compat import ImageGenerator, MockImageGenerator  # noqa: F401

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
