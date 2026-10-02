"""
Configuration for the LinkedIn Headshot Pipeline.
All API keys are loaded from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")

# ── Model Settings ────────────────────────────────────────────────────────────
# Stages 1, 2, 4 use Claude Sonnet for analysis and prompt generation
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Stage 3: face-preserving image generation via Replicate
# Options: "zsxkib/instant-id" or "lucataco/flux-dev-multi-lora" etc.
REPLICATE_MODEL = "zsxkib/instant-id:a]2b5bdc0e1b7b20ebbcb3a2aff1deb0d0a1b3b4c5d6e7f8a9b0c1d2e3f4a5b6"

# ── Generation Parameters ─────────────────────────────────────────────────────
GENERATION_PARAMS = {
    "face_id_scale": 0.85,        # How strongly the face is locked (0.6-0.95)
    "ip_adapter_scale": 0.7,      # How much style references influence output (0.5-0.9)
    "controlnet_conditioning_scale": 0.8,
    "guidance_scale": 5.0,        # How closely model follows prompt (3.0-8.0)
    "num_inference_steps": 30,    # Quality vs speed (20-50)
    "width": 1024,
    "height": 1024,
    "seed": -1,                   # -1 = random
    "negative_prompt": (
        "cartoon, painting, illustration, drawing, anime, 3d render, "
        "plastic skin, smooth skin, airbrushed, overexposed, underexposed, "
        "blurry, deformed, extra fingers, extra limbs, distorted face, doll-like"
    ),
}

# ── Validation Thresholds ─────────────────────────────────────────────────────
VALIDATION_THRESHOLDS = {
    "face_match": 85,
    "attire_accuracy": 90,
    "background": 95,
    "realism": 80,
    # cultural_sensitivity is pass/fail — no numeric threshold
}

# ── Pipeline Settings ─────────────────────────────────────────────────────────
MAX_RETRIES = 3
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
REFERENCE_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "reference_images")

# ── Reference Image Paths (populated at runtime) ─────────────────────────────
REFERENCE_IMAGES = []
if os.path.isdir(REFERENCE_IMAGES_DIR):
    for f in sorted(os.listdir(REFERENCE_IMAGES_DIR)):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            REFERENCE_IMAGES.append(os.path.join(REFERENCE_IMAGES_DIR, f))
