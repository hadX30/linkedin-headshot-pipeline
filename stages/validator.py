"""
Stage 4: Quality Validator (original import path).
Sends both the original photo and the generated headshot to Claude Vision
for a side-by-side quality check. Returns structured QA JSON.
"""

import json

from headshot.compat import QualityValidator
from headshot.prompts import VALIDATOR_SYSTEM_PROMPT  # noqa: F401

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m stages.validator <original_image> <generated_image>")
        sys.exit(1)

    validator = QualityValidator()
    result = validator.validate(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2, ensure_ascii=False))
