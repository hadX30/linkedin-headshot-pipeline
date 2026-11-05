#!/usr/bin/env python3
"""
LinkedIn Headshot Pipeline — Orchestrator
Wires all 4 stages together with retry logic and a CLI interface.

Stages 3 and 4 run as a best-of-N loop: several candidates are generated and
scored, and the highest-scoring one that clears the hard floors is delivered.

Usage:
    python pipeline.py <image_path>                    # Full pipeline
    python pipeline.py <image_path> --mock             # Mock Stage 3 (no image API)
    python pipeline.py <image_path> --analyze-only     # Run Stage 1 only
    python pipeline.py <image_path> --stage 1          # Run up to stage N
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

from config import (
    N_CANDIDATES,
    OUTPUT_DIR,
    EARLY_EXIT_COMPOSITE,
    COMPOSITE_DELIVER_THRESHOLD,
)
from stages.analyzer import PhotoAnalyzer
from stages.prompt_generator import PromptGenerator
from stages.image_generator import ImageGenerator, MockImageGenerator
from stages.validator import QualityValidator, select_best_candidate


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
        mock_generation: If True, skip the real OpenAI API call (Stage 3)
                         and copy the input as "generated" for testing.
        stop_after_stage: Stop after this stage (1-4). Default: run all.
        output_dir: Override the output directory.
        verbose: Print progress logs.

    Returns:
        dict with keys:
            status: "delivered" | "manual_review" | "error" | "partial"
            analysis: Stage 1 output (dict)
            prompt: Stage 2 output (str)
            generated_image: Path to the selected (winning) image
            final_image: Stable-named copy of the selected image
            selected_attempt: Which candidate number was chosen
            candidates_generated: How many candidates were produced
            candidate_scores: Composite score per candidate, for comparison
            validation: Stage 4 output for the selected candidate (dict)
            retries: Extra generations used beyond the first
            error: Error message if status is "error" or "manual_review"
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

    # ── Stage 3 + 4: Generate N candidates → Validate each → Select best ──
    #
    # Every candidate is kept and scored, and the winner is chosen at the end.
    # The previous design discarded each attempt the moment it failed and
    # regenerated from scratch, which meant a strong early image could be
    # thrown away and replaced by a weaker later one — the degradation spiral
    # seen in beta, where face match drifted 87 → 88 → 82 across retries.
    #
    # Validator guidance still refines the prompt between candidates, but it is
    # now risk-free: if the guidance helps, the later candidate wins on score;
    # if it hurts, the earlier candidate is still on the table and wins instead.
    if mock_generation:
        img_generator = MockImageGenerator(output_dir=out_dir)
    else:
        img_generator = ImageGenerator(output_dir=out_dir)

    validator = QualityValidator()
    current_prompt = prompt
    candidates = []

    for attempt in range(1, N_CANDIDATES + 1):
        if verbose:
            log("Stage 3", f"Generating candidate {attempt}/{N_CANDIDATES}...")

        # ── Stage 3: Image Generation ─────────────────────────────────
        try:
            output_filename = f"{ts}_{basename}_headshot_v{attempt}.png"
            generated_path = img_generator.generate(
                prompt=current_prompt,
                face_image_path=image_path,
                output_filename=output_filename,
            )
        except Exception as e:
            # A failed generation costs us one candidate, not the whole job.
            log("Stage 3", f"Candidate {attempt} failed to generate: {e}")
            if attempt < N_CANDIDATES:
                continue
            if not candidates:
                result["status"] = "error"
                result["error"] = f"Stage 3 failed on every candidate: {e}"
                return result
            break

        # Expose the newest image so a --stage 3 run still returns something.
        result["generated_image"] = generated_path

        if stop_after_stage <= 3:
            result["status"] = "partial"
            return result

        # ── Stage 4: Quality Validation ───────────────────────────────
        if verbose:
            log("Stage 4", f"Validating candidate {attempt}...")

        try:
            validation = validator.validate(image_path, generated_path, analysis=analysis)
        except Exception as e:
            # Treat an unscored candidate as unusable rather than fatal.
            log("Stage 4", f"Candidate {attempt} failed validation: {e}")
            if attempt < N_CANDIDATES:
                continue
            if not candidates:
                result["status"] = "error"
                result["error"] = f"Stage 4 failed on every candidate: {e}"
                return result
            break

        # Save this candidate's validation report
        val_path = os.path.join(out_dir, f"{ts}_{basename}_validation_v{attempt}.json")
        with open(val_path, "w") as f:
            json.dump(validation, f, indent=2, ensure_ascii=False)

        overall = validation.get("overall", {})
        composite = overall.get("composite", 0.0)

        candidates.append(
            {
                "attempt": attempt,
                "path": generated_path,
                "validation": validation,
            }
        )

        if verbose:
            for check in ["face_match", "attire_accuracy", "background", "realism"]:
                if check in validation:
                    score = validation[check].get("score", "?")
                    passed = "✓" if validation[check].get("pass") else "✗"
                    log("Stage 4", f"  {check}: {score}/100 {passed}")
            cultural = validation.get("cultural_sensitivity", {})
            c_passed = "✓" if cultural.get("pass") else "✗"
            log("Stage 4", f"  cultural_sensitivity: {c_passed}")
            log("Stage 4", f"  composite: {composite:.1f} (bar: {COMPOSITE_DELIVER_THRESHOLD})")

        # ── Early exit: this candidate is clearly good enough ─────────
        # Saves the cost of generating candidates we would not choose.
        if overall.get("floors_pass") and composite >= EARLY_EXIT_COMPOSITE:
            if verbose:
                log("Pipeline", f"Candidate {attempt} scored {composite:.1f} — stopping early")
            break

        # ── Refine the prompt for the next candidate ──────────────────
        if attempt < N_CANDIDATES:
            guidance = overall.get("retry_guidance") or ""
            if verbose and guidance:
                log("Stage 2", f"Refining prompt: {guidance}")
            try:
                current_prompt = generator.generate(analysis, retry_guidance=guidance)
                retry_prompt_path = os.path.join(
                    out_dir, f"{ts}_{basename}_prompt_retry{attempt}.txt"
                )
                with open(retry_prompt_path, "w") as f:
                    f.write(current_prompt)
            except Exception as e:
                # Keep the existing prompt; the next candidate still has value.
                log("Stage 2", f"Prompt refinement failed, reusing previous prompt: {e}")

    # ── Selection: pick the winner across all candidates ──────────────────
    if not candidates:
        result["status"] = "manual_review"
        result["error"] = "No candidate completed generation and validation."
        return result

    best = select_best_candidate(candidates)

    result["validation"] = best["validation"]
    result["generated_image"] = best["path"]
    result["selected_attempt"] = best["attempt"]
    result["candidates_generated"] = len(candidates)
    # Retained for backward compatibility: how many extra generations were used.
    result["retries"] = len(candidates) - 1

    # Score summary across all candidates, so a reviewer can see the comparison.
    result["candidate_scores"] = [
        {
            "attempt": c["attempt"],
            "composite": c["validation"].get("overall", {}).get("composite"),
            "floors_pass": c["validation"].get("overall", {}).get("floors_pass"),
        }
        for c in candidates
    ]

    # Write the winner under a stable name so downstream consumers (the API,
    # the studio team browsing the output folder) do not have to re-derive it.
    final_path = os.path.join(out_dir, f"{ts}_{basename}_headshot_FINAL.png")
    try:
        shutil.copy2(best["path"], final_path)
        result["final_image"] = final_path
    except OSError as e:
        log("Pipeline", f"Could not write final image copy: {e}")
        result["final_image"] = best["path"]

    if verbose:
        log("Pipeline", f"Generated {len(candidates)} candidate(s)")
        for c in result["candidate_scores"]:
            marker = " ← selected" if c["attempt"] == best["attempt"] else ""
            log("Pipeline", f"  candidate {c['attempt']}: composite {c['composite']}{marker}")

    if best["deliverable"]:
        result["status"] = "delivered"
        if verbose:
            log("Pipeline", f"SUCCESS — delivering candidate {best['attempt']}")
            log("Pipeline", f"Output: {result['final_image']}")
    else:
        result["status"] = "manual_review"
        overall = best["validation"].get("overall", {})
        reason = (
            "cultural sensitivity failed"
            if not best["validation"].get("cultural_sensitivity", {}).get("pass", True)
            else ", ".join(overall.get("floors_failed", []))
            or f"composite {overall.get('composite')} below {COMPOSITE_DELIVER_THRESHOLD}"
        )
        result["error"] = f"No candidate met the delivery bar — {reason}"
        if verbose:
            log("Pipeline", f"MANUAL REVIEW — {reason}")
            log("Pipeline", f"Best available: {result['final_image']}")

    return result

def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn Headshot Pipeline — Transform casual photos into professional headshots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py photo.jpg                    # Full pipeline with OpenAI API
  python pipeline.py photo.jpg --mock             # Test pipeline without API (copies input)
  python pipeline.py photo.jpg --analyze-only     # Just run the analyzer
  python pipeline.py photo.jpg --stage 2          # Run stages 1-2 only (analysis + prompt)
        """,
    )
    parser.add_argument("image", help="Path to the photo to process")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock image generator (skip OpenAI API, copies input as output)",
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

    if result.get("candidate_scores"):
        selected = result.get("selected_attempt")
        print(f"  Candidates: {result.get('candidates_generated')}")
        for c in result["candidate_scores"]:
            marker = "←" if c["attempt"] == selected else " "
            floors = "floors ok" if c["floors_pass"] else "floors FAILED"
            print(f"    {marker} candidate {c['attempt']}: composite {c['composite']} ({floors})")

    if result.get("final_image"):
        print(f"  Output: {result['final_image']}")
    elif result.get("generated_image"):
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
