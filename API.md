# Web API

Start: `.venv/bin/uvicorn api:app --port 8000`. Interactive docs: `/docs`.

## The pipeline's API

The contract of the team's `api.py`: the request runs the whole pipeline and returns its result.

### `GET /health`

`{"status": "ok"}`

### `POST /v1/headshots`

Multipart form upload in the `image` field. Runs the whole pipeline before answering.

```bash
curl -X POST http://localhost:8000/v1/headshots -F "image=@portrait.jpg"
```

- `415` `{"detail": "Unsupported image type. Upload a JPEG, PNG, or WebP image."}` for other extensions.
- `201` with `job_id` and the pipeline's result: `status`, `analysis`, `prompt`, `generated_image`, `validation`
  (the selected candidate's review), `retries`, `error`, `selected_attempt`, `candidates_generated`,
  `candidate_scores` (`[{attempt, composite, floors_pass}]`). `generated_image` is `/v1/headshots/<job_id>/image`
  when an image exists.
- `500` with the same object in `detail` when `status` is `error`.

Statuses: `delivered`, `manual_review` (no candidate was deliverable after 3, or the analyzer rejected the photo:
then `error` is `Photo rejected by analyzer: <reason>`), `error`.

### `GET /v1/headshots/{job_id}/image`

The selected candidate as PNG. On manual review it is the best candidate, for the studio team.
`404` `Headshot not found` / `Generated headshot not found`.

## For the website: background runs

The same pipeline, started in the background and polled, with upload limits and a response that leaves out
prompts, costs and sensitive analysis fields. Origins must be allowed by `CORS_ORIGINS` / `CORS_ORIGIN_REGEX`.

### `GET /v1/config`

The decision rules come from `config.py`:

```json
{ "mode": "live", "max_upload_mb": 12, "max_attempts": 3,
  "thresholds": { "face_match": 85, "attire_accuracy": 90, "background": 80, "realism": 80 },
  "decision": {
    "floors": { "face_match": 78, "attire_accuracy": 80, "background": 65 },
    "weights": { "face_match": 0.35, "attire_accuracy": 0.3, "realism": 0.2, "background": 0.15 },
    "deliver_at": 82, "max_candidates": 3 } }
```

`thresholds` are the ideal scores (reported, not required).

### `POST /v1/runs`

Multipart `image` (JPEG/PNG/WebP, checked to be a real image) and, in mock mode only, `scenario`.
`202` → `{ "run_id": "…", "status": "queued", "mode": "live", "poll_url": "/v1/runs/…" }`.
Errors: `413` too large · `415` not a supported image · `429` rate limited · `503` live mode without keys.

### `GET /v1/runs/{run_id}`

Poll until `status` is `delivered`, `manual_review` or `error`. `reviews` has one entry per candidate, as it is
reviewed:

```json
{
  "id": "…", "status": "delivered", "stage": null, "mode": "live", "attempts": 2, "selected_attempt": 2,
  "reason": "deliverable",
  "analysis": {
    "headwear": { "type": "shemagh_with_iqal", "description_ar": "" },
    "clothing": { "type": "thobe", "is_professional": true, "description_ar": "" },
    "needs_wardrobe_change": false,
    "facial_hair": { "present": true, "description_ar": "" },
    "glasses": { "present": false, "description_ar": "" },
    "photo_quality": { "face_angle": "frontal", "lighting_quality": "natural_poor", "background_type": "indoor", "has_beauty_filters": false },
    "decision": { "proceed": true, "mode": "full_edit", "reject_reason": "none" }
  },
  "reviews": [
    { "attempt": 1, "selected": false, "action": "retry", "composite": 82.0, "deliverable": false,
      "checks": { "background": { "score": 58, "floor": 65, "weight": 0.15, "below_floor": true,
                                  "threshold": 80, "passed": false, "issues_ar": [] } },
      "cultural": { "passed": true, "issues_ar": [] } },
    { "attempt": 2, "selected": true, "action": "deliver", "composite": 87.5, "deliverable": true, "checks": {}, "cultural": {} }
  ],
  "image_url": "/v1/runs/…/image",
  "error": null
}
```

`action` per candidate is the pipeline's verdict: `deliver`, `retry` (not deliverable), `manual_review` (cultural
check failed). A candidate whose image call or review failed has only `attempt`, `selected` and `action`
(`generation_failed` / `review_failed`). The `threshold` and `passed` fields compare against the ideal score.

`reason` says why a finished run ended that way: `deliverable`, `floors`, `below_bar`, `cultural`,
`analyzer_rejected` or `no_candidates`. It is informational; the pipeline decides the status.

### `GET /v1/runs/{run_id}/image`

The PNG, only when the run is `delivered`.

## Admin (dashboard and tooling)

All need `Authorization: Bearer <ADMIN_TOKEN>`; without `ADMIN_TOKEN` set they return `503`.

| Method | Path | |
|---|---|---|
| `GET` | `/admin` | the dashboard page |
| `GET` | `/admin/api/overview?days=30` | mode, models, decision rules, cost and outcome stats |
| `GET` | `/admin/api/runs?limit&offset&status&mode` | run list |
| `GET` | `/admin/api/runs/{id}` | run (with every candidate) + every span (prompts, replies, usage, cost) |
| `GET` | `/admin/api/runs/{id}/files/{input.<ext> \| attempt-N.png}` | stored images |
| `POST` | `/admin/api/runs` | test run: `image`, `mode` (`mock`/`live`), `scenario` |
| `DELETE` | `/admin/api/runs/{id}` | delete a run, its spans and images |
| `PUT` | `/admin/api/settings` | `{"mode": "mock" \| "live"}` |
| `GET` | `/admin/api/scenarios` | mock scenarios |
