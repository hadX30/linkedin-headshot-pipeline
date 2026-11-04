"""List the Claude models this account's API key can actually use.

Run from the project root with the venv active:
    python tools/list_models.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import anthropic
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("ANTHROPIC_API_KEY", "")
if not key:
    sys.exit("ANTHROPIC_API_KEY is not set in .env")

print(f"key prefix: {key[:14]}... (len {len(key)})\n")

client = anthropic.Anthropic(api_key=key)

try:
    models = client.models.list(limit=100).data
except Exception as e:
    sys.exit(f"Could not list models: {e}")

if not models:
    sys.exit("No models returned for this key.")

print(f"{len(models)} model(s) available:\n")
for m in models:
    print(f"  {m.id:<44} {getattr(m, 'display_name', '')}")

# Suggest a sensible default for this pipeline's vision + scoring work.
ids = [m.id for m in models]
for preferred in ("claude-sonnet-5", "claude-sonnet-4-5", "claude-opus-4-6"):
    match = next((i for i in ids if i.startswith(preferred)), None)
    if match:
        print(f"\nSuggested CLAUDE_MODEL for config.py:\n    {match}")
        break
else:
    print(f"\nNone of the expected Sonnet IDs found. Pick one from the list above.")
