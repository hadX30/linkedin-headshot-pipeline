"""
Stage 2: Prompt Generator (original import path).
Takes the analyzer's JSON output and writes a tailored generation prompt
based on the v3 unified prompt template.
"""

import json

from headshot.compat import PromptGenerator
from headshot.prompts import GENERATOR_SYSTEM_PROMPT, V3_BASE_TEMPLATE  # noqa: F401

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m stages.prompt_generator <analysis_json_file>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        analysis = json.load(f)

    generator = PromptGenerator()
    prompt = generator.generate(analysis)
    print(prompt)
