#!/usr/bin/env python3
"""
Backwards-compatible CLI entry point.

    python pipeline.py photo.jpg            -> python -m headshot.cli run photo.jpg
    python pipeline.py photo.jpg --mock     -> ... --mode mock
    python pipeline.py photo.jpg --live     -> ... --mode live

Other commands: python -m headshot.cli --help
"""

import sys

from headshot.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] not in ("run", "runs", "show", "stats", "scenarios", "-h", "--help"):
        mapped = ["run"]
        for arg in args:
            mapped += ["--mode", "mock"] if arg == "--mock" else ["--mode", "live"] if arg == "--live" else [arg]
        args = mapped
    raise SystemExit(main(args))
