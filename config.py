"""
Configuration for the LinkedIn Headshot Pipeline.
All API keys are loaded from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── Model Settings ────────────────────────────────────────────────────────────
# Stages 1, 2, 4 use Claude Sonnet for analysis and prompt generation
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Stage 3: face-preserving image generation via OpenAI
OPENAI_IMAGE_MODEL = "gpt-image-1"

# ── Generation Parameters ─────────────────────────────────────────────────────
GENERATION_PARAMS = {
    "size": "1024x1024",          # Output dimensions (1024x1024, 1536x1024, 1024x1536)
    "quality": "medium",          # Quality tier: low (~$0.02), medium (~$0.07), high (~$0.19)
    "output_format": "png",       # Output format: png, jpeg, webp
    "input_fidelity": "high",     # How closely the output preserves source features (high = face lock)
    "n": 1,                       # Number of images to generate
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