# Pipeline Findings — Beta Validation Round

Recorded 2026-11-03. Evidence comes from three live end-to-end runs
(`01_thobe_cafe_control`, `02_niqab_cultural`, `03_casual_studio`) plus a
measured validator-variance test. Scores quoted are real validator output, not
estimates.

---

## OPEN — needs work

### 1. The validator has no pose or posture check

**Severity: high for product quality. Not currently detected by any check.**

`03_casual_studio` delivered at composite **90.0**, with every check passing,
on an image whose posture does not read as a professional LinkedIn portrait —
uneven shoulders, a forward lean, and a seated bearing carried over from the
source photo of the subject sitting on a step stool.

The rubric scores face, attire, background, realism and cultural sensitivity.
**None of them evaluate posture.** A slouched or seated-looking result can score
90 and ship. The flaw was caught by a human reviewer, not by the pipeline.

Root cause is the documented Pose Reconstruction Ceiling: the generator anchors
to the source pose and closes only part of the gap to the target framing. The
gap here is that the pipeline cannot *tell* when that happened.

Possible direction: add a `pose_quality` check to the validator rubric
(shoulder level, upright bearing, head position, whether the result reads as
standing or seated) and decide whether it becomes a weighted check or a hard
floor. It should probably be weighted rather than a floor at first, until its
noise characteristics are measured.

### 2. The wardrobe template imposes traditional dress unconditionally

**Severity: high — cultural. Caught by the cultural gate, caused by Stage 2.**

`03_casual_studio` candidate 1 took a man wearing a cream sweatshirt and
rendered him in a full white thobe with white shemagh and black iqal. He was
wearing no traditional dress in the source.

The cultural gate correctly failed it (`cultural_sensitivity: false`,
`attire_accuracy: 60`, composite 75.2) and the retry produced an appropriate
blazer-and-shirt result. The safety net worked. But the instruction came from
our own template, `V3_BASE_TEMPLATE` in `stages/prompt_generator.py`:

> "Casual clothing without shemagh: Edit in a white thobe with a white shemagh
> and iqal."

That is unconditional. Stage 1 was more careful, recommending "a tailored dark
suit jacket with a collared white shirt, **or** a thobe with shemagh **if
culturally preferred**" — the template ignores that conditionality.

Adding religious or cultural dress a person did not choose is a serious default
for this market. The template should default to neutral professional attire and
use traditional dress only when the subject already wears it or the client asks.

Note the cost: every occurrence burns an extra generation plus validation.

### 3. Niqab behaviour is established on a single photograph

The coverage-aware face_match fix addresses a *structural* problem — scoring a
partial view against a whole-face standard — so it should generalise: it is
about how much evidence exists, not about this particular garment.

The cultural gate's behaviour across niqab variations is NOT established. We
have one photo, one niqab style, one eye-opening geometry, and that source was
a compressed video frame. Different styles (half niqab, full veil with eye
gauze, wider or narrower openings) may behave differently, and on this photo the
gate's verdict proved sensitive to validator prompt wording: it failed candidate
1 under the original prompt and passed it under an edited one.

Deliberately NOT pre-engineered. Collect real niqab cases as they arrive and
check the gate against them. The hard gate already routes genuine uncertainty to
manual review, which is the correct fallback for cases we have not seen.

### 4. Delivery margins are thin relative to scoring noise

Measured validator noise on an unchanged image: **2.4 composite points**
(`tools/measure_validator_variance.py`, 3 runs).

Delivered composites: 85.0 (thobe), 84.0 (niqab), 90.0 (casual), against a bar
of 82. The niqab case cleared the bar by 2.0 points — inside the noise band. A
re-score could plausibly flip it.

Do not tune the bar on three photographs. Revisit with a larger batch. The hard
floors, not the composite, are the real safety mechanism, and they behaved
correctly in every case tested.

---

## RESOLVED in this round

### face_match was structurally unfair to covered faces — fixed

face_match on the delivered candidate, by how much of the face was visible:

| photo  | visible face      | face_match |
|--------|-------------------|-----------|
| niqab  | eye region only   | **80**    |
| thobe  | full face, glasses| 84        |
| casual | full face         | **88**    |

The most-covered case scored lowest despite being the easiest preservation task,
and sat one point above the 78 hard floor. The validator was grading a partial
view against a whole-face standard — its own report said "Eyebrows are hidden by
the band in both, so no brow comparison is possible."

Fix: when Stage 1 reports a niqab or similar covering, the validator scores
face_match on the visible eye region as a complete evaluation and does not deduct
for hidden features. See `QualityValidator.face_is_covered` and the FACE COVERAGE
ADJUSTMENT section of the validator prompt.

Caveat on the evidence: the niqab source was a compressed video frame, which may
account for part of the 8-point gap.

### Retry spiral — fixed

The original pipeline required every check to clear its own threshold, with
background at 95. On the control photo's real scores (face 82, attire 88,
background 93, realism 80) that produced MANUAL_REVIEW on candidate 1 — a good,
shippable image rejected. The hard-floor plus weighted-composite design delivers
it. Verified against real measured scores, not simulated ones.

### Early exit was set above the delivery bar — fixed

`EARLY_EXIT_COMPOSITE` was 88 while `COMPOSITE_DELIVER_THRESHOLD` was 82, so a
deliverable candidate did not stop the loop. One run billed three generations
when candidate 1 at 85.1 was already shippable. The two are now equal.

### Best-of-N selection was choosing on noise — mitigated

Three candidates spanned 0.8 composite points while validator noise on a single
image is 2.4. Selecting the highest among them was picking a lucky scoring draw.
Extra candidates are now generated only after a real failure — a hard floor, the
cultural gate, or the bar — which are breaches far larger than the noise. When
differences are genuine the composite discriminates well: 75.2 versus 90.0 in
`03_casual_studio`.

### Background could not fail a strong image — fixed

An unmodified photo with the original café behind the subject scored face 99,
attire 99, background 40 and reached composite 84.8, clearing an 82 bar. At 15%
weight, background could not outvote a strong face and attire. It now has a hard
floor at 65.

---

## Also worth knowing

- `input_fidelity` is rejected by `gpt-image-2.5-sunburst`; the generator drops
  it and retries automatically. All results so far are at default fidelity.
- `temperature` cannot be used to stabilise scoring: models with extended
  thinking run at fixed temperature and the parameter was removed from
  `messages.create()` in anthropic SDK 1.x. Variance can be measured, not
  suppressed.
- Editing the validator prompt can move checks it was not aimed at. A
  face-coverage adjustment intended for face_match alone tripled attire noise
  (3 -> 10 point spread) and flipped a cultural verdict. Re-measure after ANY
  prompt change to this file, not only the ones that look risky.
- The cultural gate's written rule says "any change to coverage is an automatic
  fail", but its behaviour is directional — it failed a widened niqab eye opening
  and passed a slightly narrowed one. The behaviour is right; the specification
  does not describe it. Worth aligning so the gate is auditable.
