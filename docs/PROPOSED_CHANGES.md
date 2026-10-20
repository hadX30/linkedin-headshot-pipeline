# Proposed behaviour changes (need approval)

The pipeline currently behaves exactly like the original code (`main` @ `fb4d30f`),
proven by `tests/test_regression_legacy.py`. Everything below would change what the
pipeline decides, sends, or returns, so none of it is active. Items marked **flag**
are implemented and tested but **off**; the rest are described only.

To approve one, say which; it is then switched on (or implemented) and its
regression expectation is updated in the same change.

| # | Area | Today (original behaviour) | Proposal | Why | Status |
|---|---|---|---|---|---|
| P1 | Review decision | A review check that is missing from the reviewer's reply, or a missing `cultural_sensitivity`, counts as **passed**. | Count it as **failed** (retry, or manual review for the cultural gate). | A malformed review can currently deliver an image that was never checked. | flag `REVIEW_FAIL_SAFE=true` |
| P2 | Identity preservation | `input_fidelity: "high"` is configured but never sent to the image API. | Send it to models that accept it (gpt-image-1 family). | Stronger face preservation; it was clearly intended. Not documented for `gpt-image-2.5-*`, so it is not sent there. | flag `IMAGE_SEND_INPUT_FIDELITY=true` |
| P3 | Reply format | Analyzer/reviewer JSON is read from free text (fences stripped); unparsable replies end the run in error. | Use Claude structured outputs with a schema that mirrors the original JSON shapes exactly. | Removes parse failures; the schema guarantees every check is present (makes P1 moot). | described |
| P4 | Statuses | An analyzer rejection (multiple faces, no face, …) ends as `manual_review` with error `Photo rejected by analyzer: <reason>`. | A distinct `rejected` status. | The website and dashboard could tell "unusable photo" apart from "needs a human". (Already available today as `reason: "analyzer_rejected"`, without changing the status.) | described |
| P5 | Result | If an image call fails and a later attempt succeeds, the run is `delivered` but `error` still holds the earlier `Stage 3 failed (attempt N): …` message. | Clear `error` when the run ends successfully. | A delivered run should not carry an error. | described |
| P6 | Retries | Any image-generation error is retried (up to 3 attempts), including permanent ones such as a content-policy refusal or an invalid model name. | Retry only temporary errors (rate limit, timeout, 5xx); stop immediately on permanent ones. | Saves time and money on errors a retry cannot fix. | described |
| P7 | Large photos | Photos are sent byte-for-byte. Claude rejects images over 5 MB, so such uploads end in `Stage 1 failed`. | Downscale **only** photos above the provider's limit (lossless or near-lossless, long edge ≥ 2048 px). | Many phone photos exceed 5 MB. Photos under the limit stay untouched. | described |
| P8 | Orientation | Phone photos are sent with their EXIF orientation tag, not rotated. | Rotate pixels upright before sending (no other change). | Avoids models seeing sideways faces when a client does not honour EXIF. | described |
| P9 | Website text | Analysis and review text are English only. | Ask the analyzer/reviewer for short Arabic descriptions too (`description_ar`, `issues_ar`). | The Arabic website could show what was detected and why a check failed. Requires prompt additions. | described |
| P10 | Prompt wording | The edit template says "calibrated against a set of reference shots" and asks to match them, but no reference images are ever sent; it also contains an output section (resolution, sRGB, PNG/JPEG) the image API ignores. The reviewer's retry advice mentions `face_id_scale` and negative prompts, which GPT Image does not have. | Remove the reference-shot sentences and output section; have the reviewer phrase retry advice as prompt-wording changes. | Instructions the model cannot follow can only distract it. | described |
| P11 | Analyzer rules | `low_resolution` and `minor_detected` exist as reject reasons but no rule tells the model when to use them; minors get `reduced_edit`, never rejected. | Add an explicit low-resolution rule; decide the policy for minors. | Makes rejections predictable. Policy decision for minors needed. | described |
| P12 | Scores | A score sent as a string (`"92"`) makes Stage 4 fail with a type error. | Accept numeric strings. | Small robustness gain. | described |
| P13 | Original endpoint | `POST /v1/headshots` has no upload size limit, rate limit or authentication, and returns the full analysis (incl. apparent gender, age range, skin tone) and the full prompt. | Add limits/auth and trim the response, or retire it in favour of `/v1/runs`. | Cost and privacy exposure if public. (The new `/v1/runs` already has limits and a trimmed response.) | described |
| P14 | Reference images | `config.REFERENCE_IMAGES` is loaded but never used. | Either send studio references with each edit, or remove the setting. | Matches what the prompt claims, or removes dead config. | described |
| P15 | Pose vs. preservation | The edit template asks the image model to turn the face to a three-quarter view (30–45°), turn the shoulders 15–20° and re-pose the arms, while also asking it to keep the face, expression and shemagh exactly as in the source. The reviewer then compares against the source. | Keep the source pose and face angle (background, lighting and crop only), or keep the pose change but tell the reviewer it is expected. | A turned head and new pose can only be produced by redrawing the face and headwear, which is what the reviewer fails. First live run (`6f16748e`): "drape … regenerated rather than preserved", expression drift, shemagh pattern changed → cultural gate. | described |
| P16 | Background tone | The template asks for a gradient that darkens toward the edges **and** a "slight natural lens vignette"; the reviewer requires the warm light grey `#E8E6E3` at 95. | Drop the vignette line, or state that the edges stay close to `#E8E6E3`. | Two darkening instructions stack. First live run: background 68, "too dark … vignette heavier than specified". One run only; worth testing before deciding. | described |

## Not behaviour changes (already in, documented for review)

These change *how* the pipeline runs, not what it decides; the regression tests
cover them:

- **Models are configuration.** Defaults: `claude-sonnet-5-5` (Sonnet 4 is deprecated) and
  `gpt-image-2.5-sunburst` (OpenAI shuts `gpt-image-1` down on 2026-10-23). The original
  models still work: set `CLAUDE_MODEL` / `IMAGE_MODEL`.
- **Model compatibility.** Newer Claude models think by default and may return a
  `thinking` block before the text, so the text block is read by type, and models that
  think by default get a 16,000-token limit (their thinking counts toward it). Models
  that do not think by default keep the original 2,000 / 4,000 limits exactly.
- **Prompt caching.** The (identical) system prompt is marked cacheable; this lowers cost,
  not output.
- **Tracing, cost tracking, stores, dashboard, background `/v1/runs`, mock mode** run
  around the original logic and never change a decision.
- **Original `--mock` stand-in image** is now a real PNG (the original copied a JPEG into
  a `.png` file, which the reviewer then rejected as a mislabelled image). Test tooling only.
