"""Measure how much the validator's score moves on an UNCHANGED image.

This decides whether best-of-N is worth paying for.

The pipeline generates N candidates and delivers the highest composite. That is
only meaningful if the gap between candidates is larger than the validator's own
run-to-run noise. If the noise is bigger, "pick the highest" is just picking the
luckiest scoring draw, and we are paying 3x generation cost for a coin flip.

Validates the SAME image several times and reports the spread. Compare that
spread against the gap between your actual candidates.

    python tools/measure_validator_variance.py <original> <generated> [runs]
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stages.validator import QualityValidator

if len(sys.argv) < 3:
    sys.exit(__doc__)

import json

args = sys.argv[1:]
analysis = None
analysis_args = [a for a in args if a.endswith("_analysis.json")]
if analysis_args and os.path.isfile(analysis_args[0]):
    with open(analysis_args[0]) as f:
        analysis = json.load(f)
    print(f"using analysis: {os.path.basename(analysis_args[0])}")
args = [a for a in args if not a.endswith("_analysis.json")]

original, generated = args[0], args[1]
runs = int(args[2]) if len(args) > 2 else 3

for p in (original, generated):
    if not os.path.isfile(p):
        sys.exit(f"Not found: {p}")

CHECKS = ["face_match", "attire_accuracy", "background", "realism"]

print("=" * 70)
print("  VALIDATOR VARIANCE — same image, repeated scoring")
print(f"  image: {os.path.basename(generated)}")
print(f"  runs:  {runs}")
print("=" * 70)

validator = QualityValidator()
results = []

for i in range(1, runs + 1):
    print(f"\n  run {i}/{runs} ...", end=" ", flush=True)
    try:
        r = validator.validate(original, generated, analysis=analysis)
    except Exception as e:
        print(f"failed: {e}")
        continue
    row = {c: r.get(c, {}).get("score") for c in CHECKS}
    row["composite"] = r["overall"]["composite"]
    row["cultural"] = bool(r.get("cultural_sensitivity", {}).get("pass"))
    results.append(row)
    print(" ".join(f"{c.split('_')[0]}={row[c]}" for c in CHECKS) +
          f" composite={row['composite']}"
          f" cultural={'PASS' if row['cultural'] else 'FAIL'}")

if len(results) < 2:
    sys.exit("\nNeed at least 2 successful runs to measure spread.")

print("\n" + "=" * 70)
print(f"  {'check':<18} {'min':>5} {'max':>5} {'spread':>7} {'stdev':>7}")
print("  " + "-" * 50)

spreads = {}
for c in CHECKS + ["composite"]:
    vals = [r[c] for r in results if isinstance(r.get(c), (int, float))]
    if not vals:
        continue
    spread = max(vals) - min(vals)
    spreads[c] = spread
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    print(f"  {c:<18} {min(vals):>5} {max(vals):>5} {spread:>7.1f} {sd:>7.2f}")

print("=" * 70)

# The cultural gate is a hard binary gate -- it cannot be averaged or weighted,
# so an inconsistent verdict is a safety problem, not a precision problem.
verdicts = [r["cultural"] for r in results]
passes, fails = verdicts.count(True), verdicts.count(False)
print(f"\n  CULTURAL GATE: {passes} pass / {fails} fail across {len(verdicts)} runs")
if passes and fails:
    print("  *** UNSTABLE — the same image received different cultural verdicts.")
    print("      This gate decides whether culturally incorrect output ships.")
    print("      A split verdict means that decision is partly chance.")
else:
    print(f"  stable: every run returned {'PASS' if passes else 'FAIL'}")

comp_spread = spreads.get("composite", 0)
print(f"\n  Composite noise floor on an identical image: {comp_spread:.1f} points")
print()
if comp_spread < 1.0:
    print("  → Noise is small. A gap larger than this between candidates is real,")
    print("    so best-of-N selection is meaningful. Keep N as configured.")
elif comp_spread < 2.5:
    print("  → Moderate noise. Only trust candidate gaps clearly above this value.")
    print("    Best-of-N helps on spread-out candidates, not on tight clusters.")
else:
    print("  → Noise EXCEEDS the gap typically seen between candidates.")
    print("    Best-of-N is largely selecting on scoring luck, not image quality.")
    print("    Consider N_CANDIDATES = 1 and relying on the hard floors instead —")
    print("    that cuts generation cost by two thirds for the same real quality.")
print()
