"""
Runs pipeline.run_pipeline() on its own, in a fresh interpreter with nothing from
headshot/ plugged in, using the scripted clients from fakes.py. Used by
test_pipeline_unchanged.py to compare the pipeline alone with the apps running it.

    python -I tests/run_team_pipeline.py <spec.json> <out.json>
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]

import anthropic  # noqa: E402

import fakes  # noqa: E402

# The stages call anthropic.Anthropic(...) and the OpenAI name they imported.
anthropic.Anthropic = fakes.FakeAnthropic

import pipeline  # noqa: E402
from stages import image_generator  # noqa: E402

image_generator.OpenAI = fakes.FakeOpenAI

with open(sys.argv[1]) as f:
    spec = json.load(f)
fakes.Script.load(spec)
result = pipeline.run_pipeline(
    image_path=spec["image"],
    output_dir=spec["out_dir"],
    stop_after_stage=spec.get("stage", 4),
    verbose=False,
)
final = result.get("final_image") or result.get("generated_image")
with open(sys.argv[2], "w") as f:
    json.dump({
        "result": result,
        "claude_calls": fakes.Script.claude_calls,
        "image_calls": fakes.Script.image_calls,
        "final_sha": fakes.sha(open(final, "rb").read()) if final and os.path.isfile(final) else None,
    }, f, default=str)
