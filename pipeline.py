#!/usr/bin/env python3
"""
LinkedIn Headshot Pipeline — Orchestrator
Wires all 4 stages together with retry logic and a CLI interface.

Usage:
    python pipeline.py <image_path>                    # Full pipeline
    python pipeline.py <image_path> --mock             # Mock Stage 3 (no image API)
    python pipeline.py <image_path> --analyze-only     # Run Stage 1 only
    python pipeline.py <image_path> --stage 1          # Run up to stage N

Same interface and behaviour as the original pipeline.py; the work is done by the
headshot package, which also records a trace of every run. HEADSHOT_MODE=mock
switches every stage to fixtures (no API calls) for testing.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from headshot.pipeline import LegacyOutput, create_run, execute_run
from headshot.settings import Settings
from headshot.store import build_store


def run_pipeline(
    image_path: str,
    mock_generation: bool = False,
    stop_after_stage: int = 4,
    output_dir: str | None = None,
    verbose: bool = True,
) -> dict:
    """
    Run the full headshot pipeline on a single image.

    Args:
        image_path: Path to the uploaded photo.
        mock_generation: If True, skip the real image API call (Stage 3)
                         and use a stand-in image for testing.
        stop_after_stage: Stop after this stage (1-4). Default: run all.
        output_dir: Override the output directory.
        verbose: Print progress logs.

    Returns:
        dict with keys:
            status: "delivered" | "manual_review" | "error" | "partial"
            analysis: Stage 1 output (dict)
            prompt: Stage 2 output (str)
            generated_image: Path to the generated image
            validation: Stage 4 output (dict)
            retries: Number of retries used
            error: Error message if status is "error"
    """
    if verbose:
        logging.basicConfig(level=logging.INFO, format="  [%(asctime)s] [%(name)s] %(message)s", datefmt="%H:%M:%S")
    settings = Settings.from_env()
    store = build_store(settings)
    raw = Path(image_path).read_bytes()
    run_id = create_run(settings, store, raw, os.path.basename(image_path), mode=settings.mode, source="cli",
                        mock_images=mock_generation, require_keys=False)
    output = LegacyOutput(Path(output_dir) if output_dir else settings.output_dir, image_path)
    return execute_run(settings, store, run_id, stop_after_stage=stop_after_stage, output=output)


def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn Headshot Pipeline — Transform casual photos into professional headshots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py photo.jpg                    # Full pipeline
  python pipeline.py photo.jpg --mock             # Test pipeline without the image API
  python pipeline.py photo.jpg --analyze-only     # Just run the analyzer
  python pipeline.py photo.jpg --stage 2          # Run stages 1-2 only (analysis + prompt)
        """,
    )
    parser.add_argument("image", help="Path to the photo to process")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock image generator (skip the image API; Claude stages still run)",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Run only Stage 1 (photo analysis)",
    )
    parser.add_argument(
        "--stage",
        type=int,
        choices=[1, 2, 3, 4],
        default=4,
        help="Stop after this stage (default: 4, run all)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom output directory (default: ./output/)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress logs",
    )

    args = parser.parse_args()

    # Validate input
    if not os.path.isfile(args.image):
        print(f"Error: File not found: {args.image}")
        sys.exit(1)

    if args.analyze_only:
        args.stage = 1

    print(f"\n{'='*60}")
    print(f"  LinkedIn Headshot Pipeline")
    print(f"  Input: {args.image}")
    print(f"  Mode: {'mock' if args.mock else 'live'}")
    print(f"  Stages: 1-{args.stage}")
    print(f"{'='*60}\n")

    start_time = time.time()

    result = run_pipeline(
        image_path=args.image,
        mock_generation=args.mock,
        stop_after_stage=args.stage,
        output_dir=args.output_dir,
        verbose=not args.quiet,
    )

    elapsed = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"  Result: {result['status']}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"  Retries: {result['retries']}")
    if result.get("generated_image"):
        print(f"  Output: {result['generated_image']}")
    if result.get("error"):
        print(f"  Error: {result['error']}")
    print(f"{'='*60}\n")

    # Print full analysis if verbose
    if result.get("analysis"):
        analysis_json = json.dumps(result["analysis"], indent=2, ensure_ascii=False)
        print(f"Analysis:\n{analysis_json}\n")

    # Print validation if available
    if result.get("validation"):
        val_json = json.dumps(result["validation"], indent=2, ensure_ascii=False)
        print(f"Validation:\n{val_json}\n")

    # Exit code: 0 for delivered, 1 for manual_review, 2 for error
    if result["status"] == "delivered":
        sys.exit(0)
    elif result["status"] == "manual_review":
        sys.exit(1)
    elif result["status"] == "partial":
        sys.exit(0)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
