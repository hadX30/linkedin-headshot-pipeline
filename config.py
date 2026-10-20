"""
Configuration for the LinkedIn Headshot Pipeline (original module, kept for imports).
All values now come from headshot.settings (environment variables / .env).
"""

import os

from headshot.settings import Settings

_settings = Settings.from_env()

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _settings.anthropic_api_key
OPENAI_API_KEY = _settings.openai_api_key

# ── Model Settings ────────────────────────────────────────────────────────────
CLAUDE_MODEL = _settings.claude_model
OPENAI_IMAGE_MODEL = _settings.image_model

# ── Generation Parameters ─────────────────────────────────────────────────────
GENERATION_PARAMS = dict(_settings.generation_params)

# ── Validation Thresholds ─────────────────────────────────────────────────────
VALIDATION_THRESHOLDS = dict(_settings.thresholds)

# ── Pipeline Settings ─────────────────────────────────────────────────────────
MAX_RETRIES = _settings.max_retries
OUTPUT_DIR = str(_settings.output_dir)
REFERENCE_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "reference_images")

# ── Reference Image Paths (populated at runtime) ─────────────────────────────
REFERENCE_IMAGES = []
if os.path.isdir(REFERENCE_IMAGES_DIR):
    for f in sorted(os.listdir(REFERENCE_IMAGES_DIR)):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            REFERENCE_IMAGES.append(os.path.join(REFERENCE_IMAGES_DIR, f))
