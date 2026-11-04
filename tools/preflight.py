"""Verify every API and model setting before spending money on a run.

Checks auth and model availability for both providers, and confirms the IDs in
config.py actually exist for this account. Lists models only -- generates
nothing, so running this costs nothing.

    python tools/preflight.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    CLAUDE_MODEL,
    OPENAI_IMAGE_MODEL,
    GENERATION_PARAMS,
    N_CANDIDATES,
)

ok = True


def check(label, passed, detail=""):
    global ok
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not passed:
        ok = False
    return passed


print("=" * 62)
print("  PREFLIGHT")
print("=" * 62)

# ── Anthropic ─────────────────────────────────────────────────────────
print("\nAnthropic (stages 1, 2, 4)")
if not check("key present", bool(ANTHROPIC_API_KEY)):
    pass
else:
    print(f"         prefix {ANTHROPIC_API_KEY[:14]}... len {len(ANTHROPIC_API_KEY)}")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        ids = [m.id for m in client.models.list(limit=100).data]
        check("auth + model list", True, f"{len(ids)} models")
        if not check(f"CLAUDE_MODEL '{CLAUDE_MODEL}' available", CLAUDE_MODEL in ids):
            print("         available:", ", ".join(ids[:8]))
    except Exception as e:
        check("auth + model list", False, str(e)[:120])

# ── OpenAI ────────────────────────────────────────────────────────────
print("\nOpenAI (stage 3)")
if not check("key present", bool(OPENAI_API_KEY)):
    pass
else:
    print(f"         prefix {OPENAI_API_KEY[:14]}... len {len(OPENAI_API_KEY)}")
    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)
        ids = [m.id for m in client.models.list().data]
        check("auth + model list", True, f"{len(ids)} models")

        image_models = sorted(i for i in ids if "image" in i.lower())
        if image_models:
            print("         image models:", ", ".join(image_models))

        if not check(f"OPENAI_IMAGE_MODEL '{OPENAI_IMAGE_MODEL}' available",
                     OPENAI_IMAGE_MODEL in ids):
            if image_models:
                print(f"         → consider: {image_models[-1]}")
    except Exception as e:
        check("auth + model list", False, str(e)[:120])

# ── Settings sanity ───────────────────────────────────────────────────
print("\nGeneration settings")
print(f"         {GENERATION_PARAMS}")
check("quality is a valid tier",
      GENERATION_PARAMS.get("quality") in {"auto", "low", "medium", "high", "xhigh", "max"},
      GENERATION_PARAMS.get("quality", "?"))
check("input_fidelity set", bool(GENERATION_PARAMS.get("input_fidelity")),
      GENERATION_PARAMS.get("input_fidelity", "not set"))

# ── Cost estimate ─────────────────────────────────────────────────────
# Published per-image rates at 1024x1024. gpt-image-2.5 pricing is token-based
# and OpenAI does not publish a flat per-tier figure, so the mid tiers here are
# interpolated from the stated $0.005 (low) to $0.211 (max) range and should be
# treated as approximate. Verify against your actual usage dashboard after the
# first real run rather than trusting these numbers.
RATES = {
    "gpt-image-2.5-sunburst": {"low": 0.006, "medium": 0.015, "high": 0.06,
                               "xhigh": 0.12, "max": 0.211},
    "gpt-image-2.5-flare":    {"low": 0.005, "medium": 0.013, "high": 0.06,
                               "xhigh": 0.12, "max": 0.211},
}
RETIRED = {
    "gpt-image-1": "retired 2026-10-23",
    "gpt-image-1.5": "retires 2026-12-01",
    "gpt-image-1-mini": "retires 2026-12-01",
}

base = OPENAI_IMAGE_MODEL.split("-2026-")[0]
if base in RETIRED:
    check(f"image model not retired", False, f"{base} {RETIRED[base]}")

quality = GENERATION_PARAMS.get("quality", "medium")
rate = RATES.get(base, {}).get(quality)

print("\nCost estimate (worst case, no early exit)")
if rate:
    print(f"         {quality} @ 1024x1024 ~ ${rate:.3f}/image (approximate)")
    print(f"         {N_CANDIDATES} candidates = ~${N_CANDIDATES * rate:.2f} per photo")
    print(f"         3 photos: up to ~${3 * N_CANDIDATES * rate:.2f}")
    print(f"         early exit typically cuts this by a third to a half")
else:
    print(f"         No published rate on file for {base} at '{quality}'.")
    print(f"         Worst case is {3 * N_CANDIDATES} generations across 3 photos —")
    print(f"         check your OpenAI usage dashboard after the first run.")

print("\n" + "=" * 62)
print("  READY — safe to run the pipeline" if ok else "  NOT READY — fix the FAIL items above")
print("=" * 62)
sys.exit(0 if ok else 1)
