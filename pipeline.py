#!/usr/bin/env python3
"""
LinkedIn Headshot Pipeline — Orchestrator
Wires all 4 stages together with retry logic and a CLI interface.

Usage:
    python pipeline.py <image_path>                    # Full pipeline
    python pipeline.py <image_path> --mock             # Mock Stage 3 (no Replicate API)
    python pipeline.py <image_path> --analyze-only     # Run Stage 1 only
    python pipeline.py <image_path> --stage 1          # Run up to stage N
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from config import MAX_RETRIES, OUTPUT_DIR
from stages.analyzer import PhotoAnalyzer
from stages.prompt_generator import PromptGenerator
from stages.image_generator import ImageGenerator, MockImageGenerator
from stages.validator import QualityValidator


def log(stage: str, message: str) -> None:
    """Print a timestamped log message."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] [{stage}] {message}")


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
        mock_generation: If True, skip the real Replicate API call (Stage 3)
                         and copy the input as "generated" for testing.
        stop_after_stage: Stop after this stage (1-4). Default: run all.
        output_dir: Override the output directory.
        verbose: Print progress logs.

    Returns:
        dict with keys:
            status: "delivered" | "manual_review" | "error"
            analysis: Stage 1 output (dict)
            prompt: Stage 2 output (str)
            generated_image: Path to the generated image
            validation: Stage 4 output (dict)
            retries: Number of retries used
            error: Error message if status is "error"
    """
    out_dir = output_dir or OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    result = {
        "status": None,
        "analysis": None,
        "prompt": None,
        "generated_image": None,
        "validation": None,
        "retries": 0,
        "error": None,
    }

    # Generate a timestamp-based prefix for output files
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    basename = os.path.splitext(os.path.basename(image_path))[0]

    # ── Stage 1: Photo Analyzer ───────────────────────────────────────────
    if verbose:
        log("Stage 1", "Analyzing photo...")

    try:
        analyzer = PhotoAnalyzer()
        analysis = analyzer.analyze(image_path)
        result["analysis"] = analysis
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Stage 1 failed: {e}"
        return result

    # Save analysis to file
    analysis_path = os.path.join(out_dir, f"{ts}_{basename}_analysis.json")
    with open(analysis_path, "w") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    if verbose:
        action = analysis.get("pipeline_action", {})
        log("Stage 1", f"Mode: {action.get('mode', 'unknown')}")
        log("Stage 1", f"Proceed: {action.get('proceed', False)}")
        if action.get("notes"):
            log("Stage 1", f"Notes: {action['notes']}")

    # Check if the pipeline should reject
    if not analysis.get("pipeline_action", {}).get("proceed", False):
        reason = analysis.get("pipeline_action", {}).get("reject_reason", "unknown")
        result["status"] = "manual_review"
        result["error"] = f"Photo rejected by analyzer: {reason}"
        if verbose:
            log("Stage 1", f"REJECTED — {reason}")
        return result

    if stop_after_stage <= 1:
        result["status"] = "partial"
        return result

    # ── Stage 2: Prompt Generator ─────────────────────────────────────────
    if verbose:
        log("Stage 2", "Generating tailored prompt...")

    try:
        generator = PromptGenerator()
        prompt = generator.generate(analysis)
        result["prompt"] = prompt
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"Stage 2 failed: {e}"
        return result

    # Save the prompt
    prompt_path = os.path.join(out_dir, f"{ts}_{basename}_prompt.txt")
    with open(prompt_path, "w") as f:
        f.write(prompt)

    if verbose:
        log("Stage 2", f"Prompt generated ({len(prompt)} chars)")

    if stop_after_stage <= 2:
        result["status"] = "partial"
        return result

    # ── Stage 3 + 4: Generate → Validate → Retry Loop ────────────────────
    if mock_generation:
        img_generator = MockImageGenerator(output_dir=out_dir)
    else:
        img_generator = ImageGenerator(output_dir=out_dir)

    validator = QualityValidator()
    current_prompt = prompt
    retry_guidance = None

    for attempt in range(1, MAX_RETRIES + 1):
        if verbose:
            log("Stage 3", f"Generating headshot (attempt {attempt}/{MAX_RETRIES})...")

        # ── Stage 3: Image Generation ─────────────────────────────────
        try:
            output_filename = f"{ts}_{basename}_headshot_v{attempt}.png"
            generated_path = img_generator.generate(
                prompt=current_prompt,
                face_image_path=image_path,
                output_filename=output_filename,
            )
            result["generated_image"] = generated_path
        except Exception as e:
            result["status"] = "error"
            result["error"] = f"Stage 3 failed (attempt {attempt}): {e}"
            if attempt < MAX_RETRIES:
                log("Stage 3", f"Generation failed, retrying... ({e})")
                continue
            return result

        if stop_after_stage <= 3:
            result["status"] = "partial"
            return result

        # ── Stage 4: Quality Validation ───────────────────────────────
        if verbose:
            log("Stage 4", "Validating result...")

        try:
            validation = validator.validate(image_path, generated_path)
            result["validation"] = validation
        except Exception as e:
            result["status"] = "error"
            result["error"] = f"Stage 4 failed: {e}"
            return result

        # Save validation report
        val_path = os.path.join(out_dir, f"{ts}_{basename}_validation_v{attempt}.json")
        with open(val_path, "w") as f:
            json.dump(validation, f, indent=2, ensure_ascii=False)

        overall = validation.get("overall", {})
        action = overall.get("action", "manual_review")

        if verbose:
            log("Stage 4", f"Verdict: {action}")
            for check in ["face_match", "attire_accuracy", "background", "realism"]:
                if check in validation:
                    score = validation[check].get("score", "?")
                    passed = "✓" if validation[check].get("pass") else "✗"
                    log("Stage 4", f"  {check}: {score}/100 {passed}")
            cultural = validation.get("cultural_sensitivity", {})
            c_passed = "✓" if cultural.get("pass") else "✗"
            log("Stage 4", f"  cultural_sensitivity: {c_passed}")

        # ── Decision ──────────────────────────────────────────────────
        if action == "deliver":
            result["status"] = "delivered"
            result["retries"] = attempt - 1
            if verbose:
                log("Pipeline", f"SUCCESS — Headshot delivered after {attempt} attempt(s)")
                log("Pipeline", f"Output: {generated_path}")
            return result

        elif action == "retry" and attempt < MAX_RETRIES:
            retry_guidance = overall.get("retry_guidance", "")
            result["retries"] = attempt
            if verbose:
                log("Stage 4", f"Retrying with guidance: {retry_guidance}")

            # Regenerate the prompt with retry feedback
            try:
                current_prompt = generator.generate(analysis, retry_guidance=retry_guidance)
                # Save retry prompt
                retry_prompt_path = os.path.join(
                    out_dir, f"{ts}_{basename}_prompt_retry{attempt}.txt"
                )
                with open(retry_prompt_path, "w") as f:
                    f.write(current_prompt)
            except Exception as e:
                log("Stage 2", f"Retry prompt generation failed: {e}")
                # Continue with the existing prompt

        else:
            # Either manual_review or retries exhausted
            result["status"] = "manual_review"
            result["retries"] = attempt
            if verbose:
                if attempt >= MAX_RETRIES:
                    log("Pipeline", f"Max retries ({MAX_RETRIES}) exhausted — routing to manual review")
                else:
                    log("Pipeline", f"Routing to manual review — {action}")
                log("Pipeline", f"Last output: {generated_path}")
            return result

    # Should not reach here, but just in case
    result["status"] = "manual_review"
    return result


def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn Headshot Pipeline — Transform casual photos into professional headshots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py photo.jpg                    # Full pipeline with Replicate API
  python pipeline.py photo.jpg --mock             # Test pipeline without API (copies input)
  python pipeline.py photo.jpg --analyze-only     # Just run the analyzer
  python pipeline.py photo.jpg --stage 2          # Run stages 1-2 only (analysis + prompt)
        """,
    )
    parser.add_argument("image", help="Path to the photo to process")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock image generator (skip Replicate API, copies input as output)",
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
