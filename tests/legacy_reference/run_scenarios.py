"""
Runs the frozen ORIGINAL pipeline (this folder, copied from main @ fb4d30f) on
scripted scenarios and writes what it did to a JSON file.

    python -I run_scenarios.py <scenarios.json> <photo> <results.json>

Started in its own process by tests/test_regression_legacy.py, so the original
`config`, `stages` and `pipeline` modules never mix with the new ones.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))      # the original config/stages/pipeline (-I does not add it)
sys.path.append(str(HERE.parent))  # tests/: regression_fakes (after this folder, so originals win)

import anthropic  # noqa: E402
import openai  # noqa: E402

import regression_fakes as F  # noqa: E402

anthropic.Anthropic = F.FakeAnthropic  # the stages call anthropic.Anthropic(...)
openai.OpenAI = F.FakeOpenAI           # image_generator does `from openai import OpenAI` at import

from pipeline import run_pipeline  # noqa: E402  (the original)

specs = json.loads(Path(sys.argv[1]).read_text())
photo = sys.argv[2]
results = {}
for name, spec in specs.items():
    F.Script.load(spec)
    with tempfile.TemporaryDirectory() as out_dir:
        result = run_pipeline(photo, output_dir=out_dir, verbose=False)
        generated = result.get("generated_image")
        result["generated_image_sha"] = F.sha(Path(generated).read_bytes()) if generated and os.path.isfile(generated) else None
        result["generated_image"] = bool(generated)
    results[name] = {
        "result": result,
        "claude_calls": F.Script.claude_calls,
        "image_calls": F.Script.image_calls,
        "unused": [len(F.Script.claude), len(F.Script.images)],
    }
Path(sys.argv[3]).write_text(json.dumps(results, default=str))
