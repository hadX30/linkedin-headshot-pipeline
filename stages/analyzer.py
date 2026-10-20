"""
Stage 1: Photo Analyzer (original import path).
Sends the uploaded photo to Claude Vision and extracts structured JSON
describing the subject, attire, photo quality, and pipeline action.
"""

import json

from headshot.compat import PhotoAnalyzer
from headshot.prompts import ANALYZER_SYSTEM_PROMPT  # noqa: F401

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m stages.analyzer <image_path>")
        sys.exit(1)

    analyzer = PhotoAnalyzer()
    result = analyzer.analyze(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
