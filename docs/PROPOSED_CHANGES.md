# Proposed behaviour changes

The pipeline (`pipeline.py`, `stages/`, `config.py`) is the team's code, as of `40dbe16` (best-of-N candidates,
hard floors plus a weighted composite; see FINDINGS.md). The apps run it unchanged
(`tests/test_pipeline_unchanged.py`). The items below are suggestions for the team; none is active unless the
pipeline adopts it.

| # | Area | Today | Proposal | Why | Status |
|---|---|---|---|---|---|
| P1 | Missing review data | A missing face, attire or background score fails its floor. A missing realism score is left out of the composite, and a missing `cultural_sensitivity` counts as passed. | Treat a missing cultural verdict as a failure too. | A malformed review should not pass the cultural check by default. | partly adopted (40dbe16) |
| P2 | Identity preservation | `input_fidelity: "high"` is sent; `gpt-image-2.5-sunburst` rejects it, so the generator drops it and retries. | — | — | adopted (40dbe16) |
| P3 | Reply format | Analyzer/reviewer JSON is read from free text (fences stripped); an unparsable review skips that candidate. | Use Claude structured outputs with a schema matching the JSON shapes. | Removes parse failures and guarantees every check is present. | open |
| P4 | Statuses | An analyzer rejection ends as `manual_review` with error `Photo rejected by analyzer: <reason>`. | A distinct `rejected` status. | "Unusable photo" vs "needs a human" (available today as `reason: analyzer_rejected`). | open |
| P6 | Retries | Any image-generation error skips that candidate, including permanent ones (content policy, invalid model). | Stop on permanent errors; skip only temporary ones (rate limit, timeout, 5xx). | Saves candidates a retry cannot fix. | open |
| P7 | Large photos | Photos are sent byte-for-byte; Claude rejects images over 5 MB (`Stage 1 failed`). | Downscale only photos above the provider's limit. | Many phone photos exceed 5 MB. | open |
| P8 | Orientation | Photos are sent with their EXIF orientation tag, not rotated. | Rotate pixels upright before sending. | Models may see sideways faces. | open |
| P9 | Website text | Analysis and review text are English only. | Ask for short Arabic descriptions too (`description_ar`, `issues_ar`). | The Arabic website could show what was detected and why a check failed. | open |
| P10 | Prompt wording | The reviewer now phrases retry advice as prompt wording (adopted). The edit template still says it is "calibrated against reference shots" (none are sent) and has an output section the image API ignores. | Remove those two parts of the template. | Instructions the model cannot follow can only distract it. | partly adopted (40dbe16) |
| P11 | Analyzer rules | `low_resolution` and `minor_detected` exist as reject reasons, but no rule says when to use them. | Add an explicit low-resolution rule; decide the policy for minors. | Predictable rejections. | open |
| P12 | Scores | A score sent as a string (`"92"`) fails the floor comparison, so the candidate is skipped. | Accept numeric strings. | Small robustness gain. | open |
| P13 | `POST /v1/headshots` | No upload size limit, rate limit or authentication; returns the full analysis and prompt. | Add limits/auth and trim the response, or retire it in favour of `/v1/runs`. | Cost and privacy exposure on a public URL. | open |
| P14 | Reference images | `config.REFERENCE_IMAGES` is loaded but never used. | Send studio references with each edit, or remove the setting. | Matches what the prompt claims, or removes dead config. | open |
| P15 | Pose vs. preservation | The template asks for a three-quarter turn and a new pose while keeping the face and headwear exactly as in the source. | Keep the source pose, or tell the reviewer pose changes are expected. Related: FINDINGS.md #1 (no posture check). | A turned head can only be produced by redrawing face and headwear, which the reviewer then penalises. | open |
| P16 | Background tone | The reviewer now accepts any clean neutral grey (adopted). The template still asks for both a darkening gradient and a lens vignette. | Drop the vignette line. | Two darkening instructions stack. | partly adopted (40dbe16) |
| P17 | Token limits | The stages ask for 2,000 / 4,000 / 2,000 tokens. `claude-sonnet-5-5` thinks by default, and thinking counts toward the limit: in the first live run, the review used 2,459 output tokens and the prompt 3,401. | Raise the limits for models that think by default. | A cut-off reply (`stop_reason: max_tokens`, visible in the dashboard) cannot be parsed, so the candidate is skipped and another image is billed. | open |
| P18 | `pipeline.py --mock` | `MockImageGenerator` copies the input photo (often a JPEG) under a `.png` name, and the reviewer is told it is PNG. | Convert the copy to PNG. | The reviewer receives a mislabelled image in this test mode. | open (CLI test mode only) |

P5 (a stale error on delivered runs) no longer applies: a failed candidate is now skipped without setting `error`.
