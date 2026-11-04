"""Re-score candidates that were already generated, without regenerating them.

Stage 3 is the expensive part. When a run produces images but fails in Stage 4,
the images on disk are still perfectly good -- only the scoring needs redoing.
This validates them in place and shows which one the pipeline would deliver.

    python tools/rescore_run.py <original_image> <candidate.png> [candidate.png ...]
    python tools/rescore_run.py test_images/01_thobe_cafe_control.png output/*_v*.png
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import COMPOSITE_DELIVER_THRESHOLD, HARD_FLOORS
from stages.validator import QualityValidator, select_best_candidate

if len(sys.argv) < 3:
    sys.exit(__doc__)

original = sys.argv[1]
paths = [a for a in sys.argv[2:] if not a.endswith("_analysis.json")]

# An analysis JSON may be passed among the arguments. It is what tells the
# validator a face covering is present, so a re-score without it can score
# face_match differently from the live run it is reproducing.
analysis = None
for a in sys.argv[2:]:
    if a.endswith("_analysis.json") and os.path.isfile(a):
        with open(a) as f:
            analysis = json.load(f)
        print(f"  using analysis: {os.path.basename(a)}")
        break

for p in [original, *paths]:
    if not os.path.isfile(p):
        sys.exit(f"Not found: {p}")

print("=" * 70)
print("  RE-SCORING EXISTING CANDIDATES (no image generation)")
print(f"  original:   {os.path.basename(original)}")
print(f"  candidates: {len(paths)}")
print(f"  bar:        composite >= {COMPOSITE_DELIVER_THRESHOLD}, floors {HARD_FLOORS}")
print("=" * 70)

validator = QualityValidator()
candidates = []

for i, path in enumerate(paths, start=1):
    print(f"\n[{i}/{len(paths)}] {os.path.basename(path)}")
    try:
        report = validator.validate(original, path, analysis=analysis)
    except Exception as e:
        print(f"    validation failed: {e}")
        continue

    o = report["overall"]
    for check in ["face_match", "attire_accuracy", "background", "realism"]:
        if check in report:
            s = report[check].get("score", "?")
            mark = "ok " if report[check].get("pass") else "LOW"
            issues = report[check].get("issues") or []
            print(f"    {check:<18} {s:>3}  {mark}" +
                  (f"   {issues[0][:60]}" if issues else ""))
    cult = report.get("cultural_sensitivity", {})
    print(f"    {'cultural':<18} {'PASS' if cult.get('pass') else 'FAIL':>4}" +
          (f"   {(cult.get('issues') or ['-'])[0][:60]}" if cult.get("issues") else ""))
    print(f"    {'COMPOSITE':<18} {o['composite']:>5}   floors_pass={o['floors_pass']}")
    if o.get("floors_failed"):
        print(f"    floors failed: {', '.join(o['floors_failed'])}")

    candidates.append({"attempt": i, "path": path, "validation": report})

    # Persist the report next to the image.
    out = os.path.splitext(path)[0] + "_rescored.json"
    with open(out, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

if not candidates:
    sys.exit("\nNo candidate could be scored.")

best = select_best_candidate(candidates)

print("\n" + "=" * 70)
print("  SELECTION")
for c in candidates:
    o = c["validation"]["overall"]
    mark = "  <-- SELECTED" if c["attempt"] == best["attempt"] else ""
    print(f"    candidate {c['attempt']}: composite {o['composite']:>5}  "
          f"floors={'ok' if o['floors_pass'] else 'FAIL'}{mark}")
print()
if best["deliverable"]:
    print(f"  VERDICT: DELIVER candidate {best['attempt']}")
else:
    o = best["validation"]["overall"]
    reason = ", ".join(o.get("floors_failed") or []) or \
             f"composite {o['composite']} < {COMPOSITE_DELIVER_THRESHOLD}"
    print(f"  VERDICT: MANUAL REVIEW — {reason}")
    print(f"           best available is candidate {best['attempt']}")
print(f"  file:    {best['path']}")
print("=" * 70)
