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
# These mark IDEAL quality per check. They are reported in the QA record but
# they are no longer the delivery bar on their own — see the delivery decision
# block below. Requiring every check to independently clear its ideal score is
# what caused the beta retry spiral: one unreachable check (background at 95)
# forced regeneration of images that were already good.
'''
VALIDATION_THRESHOLDS = {
    "face_match": 85,
    "attire_accuracy": 90,
    "background": 80,   # was 95 — unreachable for generative output, see note
    "realism": 80,
    # cultural_sensitivity is pass/fail — no numeric threshold
}
'''
# ── Delivery Decision ─────────────────────────────────────────────────────────
# Two-layer decision. Layer 1: hard floors a candidate MUST clear. These are
# correctness gates — below them the image is WRONG, not merely imperfect, and
# no composite score can compensate.
HARD_FLOORS = {
    "face_match": 78,        # below this it stops reading as the same person
    "attire_accuracy": 80,   # below this the garment is misrepresented
    "background": 65,        # below this the ORIGINAL environment is still visible
}
# Why background needs a floor and not just a weight: at 15% weight, a total
# background failure cannot outvote a strong face and attire. A real case from
# the output folder -- an unmodified photo, cafe still behind the subject --
# scored face 99, attire 99, background 40, and reached composite 84.8, clearing
# an 82 bar. It would have shipped with the coffee shop in frame. Background
# replacement is the core transformation this product sells; failing it is a
# product failure, not a quality deduction, so it gets a gate of its own.
#
# The floor sits at 65 rather than at the rubric's 70 boundary to leave margin
# for residual scoring noise. Observed failures of this kind score 40-58, well
# clear of 65, while a merely-flawed-but-clean backdrop scores 70-79 and passes.
# cultural_sensitivity remains an absolute pass/fail gate, handled separately.

# Layer 2: a weighted composite across the four numeric checks. Weights reflect
# what each failure actually costs the client:
#   face_match  — a face that isn't theirs is a total product failure
#   attire      — cultural garments carry identity; errors are not acceptable
#   realism     — an obviously-AI headshot undermines the whole offering
#   background  — easiest to fix in post, least visible to a LinkedIn viewer
COMPOSITE_WEIGHTS = {
    "face_match": 0.35,
    "attire_accuracy": 0.30,
    "realism": 0.20,
    "background": 0.15,
}

# Deliver when every hard floor passes, cultural passes, and the composite
# clears this bar.
COMPOSITE_DELIVER_THRESHOLD = 82

# Stop generating further candidates as soon as one is DELIVERABLE.
#
# This is deliberately equal to COMPOSITE_DELIVER_THRESHOLD, not above it.
# It was previously set to 88 while the delivery bar was 82, which meant a
# candidate scoring 85 -- already good enough to ship -- did not stop the loop,
# and the pipeline kept paying for candidates it would never choose. A real run
# produced three candidates at 85.1 / 85.8 / 85.0 and billed for all three when
# the first was already deliverable.
#
# There is also no point selecting between candidates this close together.
# Measured on an unchanged image, the validator's own composite varies by about
# 2.4 points run to run, while those three candidates spanned 0.8 points. The
# gap is well inside the noise, so "pick the highest" was choosing on scoring
# luck rather than image quality. Generating more candidates only earns its cost
# when the first one fails for a reason larger than that noise -- a hard-floor
# breach or the cultural gate -- which is exactly when this early exit does not
# fire.
EARLY_EXIT_COMPOSITE = COMPOSITE_DELIVER_THRESHOLD

# ── Pipeline Settings ─────────────────────────────────────────────────────────
MAX_RETRIES = _settings.max_retries
OUTPUT_DIR = str(_settings.output_dir)
# Upper bound on candidates, not a target. With the early exit above, a
# deliverable first candidate ends the loop immediately, so the common case
# costs ONE generation. Further candidates are produced only when a candidate
# genuinely fails -- a hard floor, the cultural gate, or a composite below the
# bar -- and every candidate is kept, so a worse later one can never displace a
# better earlier one.
N_CANDIDATES = 3
'''
# Retained for backward compatibility with older callers.
MAX_RETRIES = N_CANDIDATES
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
REFERENCE_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "reference_images")
'''
# ── Reference Image Paths (populated at runtime) ─────────────────────────────
REFERENCE_IMAGES = []
if os.path.isdir(REFERENCE_IMAGES_DIR):
    for f in sorted(os.listdir(REFERENCE_IMAGES_DIR)):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            REFERENCE_IMAGES.append(os.path.join(REFERENCE_IMAGES_DIR, f))
