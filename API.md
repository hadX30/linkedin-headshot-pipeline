# Web API

Start: `.venv/bin/uvicorn api:app --port 8000`. Interactive docs: `/docs`.

## Original endpoints (unchanged)

Same requests, responses, status codes and messages as the original `api.py`.

### `GET /health`

`{"status": "ok"}`

### `POST /v1/headshots`

Multipart form upload in the `image` field. Runs the whole pipeline before answering.

```bash
curl -X POST http://localhost:8000/v1/headshots -F "image=@portrait.jpg"
```

- `415` `{"detail": "Unsupported image type. Upload a JPEG, PNG, or WebP image."}` for other extensions.
- `201` with `job_id`, `status`, `analysis`, `prompt`, `generated_image`, `validation`, `retries`, `error`.
  `generated_image` is `/v1/headshots/<job_id>/image` when an image exists.
- `500` with the same object in `detail` when `status` is `error`.

Statuses: `delivered`, `manual_review` (cultural gate, 3+ failed checks, attempts used up, or rejected by the
analyzer: then `error` is `Photo rejected by analyzer: <reason>`), `error`.

### `GET /v1/headshots/{job_id}/image`

The latest generated attempt as PNG (also when the run went to manual review).
`404` `Headshot not found` / `Generated headshot not found`.

## New, separate: background runs (used by the website)

The same pipeline, started in the background and polled, with upload limits and a response that leaves out
prompts, costs and sensitive analysis fields. Origins must be allowed by `CORS_ORIGINS` / `CORS_ORIGIN_REGEX`.

### `GET /v1/config`

```json
{ "mode": "live", "max_upload_mb": 12, "max_attempts": 3,
  "thresholds": { "face_match": 85, "attire_accuracy": 90, "background": 95, "realism": 80 } }
```

### `POST /v1/runs`

Multipart `image` (JPEG/PNG/WebP, checked to be a real image) and, in mock mode only, `scenario`.
`202` → `{ "run_id": "…", "status": "queued", "mode": "live", "poll_url": "/v1/runs/…" }`.
Errors: `413` too large · `415` not a supported image · `429` rate limited · `503` live mode without keys.

### `GET /v1/runs/{run_id}`

Poll until `status` is `delivered`, `manual_review` or `error` (the original statuses).

```json
{
  "id": "…", "status": "running", "stage": "generate", "mode": "live", "attempts": 1,
  "reason": null,
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
    { "attempt": 1, "action": "retry",
      "checks": { "face_match": { "score": 82, "threshold": 85, "passed": false, "issues_ar": [] } },
      "cultural": { "passed": true, "issues_ar": [] } }
  ],
  "image_url": null,
  "error": null
}
```

`reason` says why a finished run ended that way: `passed`, `cultural`, `multiple_checks`, `max_attempts`,
`analyzer_rejected`. It is informational; the status is decided by the original rules.

### `GET /v1/runs/{run_id}/image`

The PNG, only when the run is `delivered`.

## Admin (dashboard and tooling)

All need `Authorization: Bearer <ADMIN_TOKEN>`; without `ADMIN_TOKEN` set they return `503`.

| Method | Path | |
|---|---|---|
| `GET` | `/admin` | the dashboard page |
| `GET` | `/admin/api/overview?days=30` | mode, models, thresholds, proposal flags, cost and outcome stats |
| `GET` | `/admin/api/runs?limit&offset&status&mode` | run list |
| `GET` | `/admin/api/runs/{id}` | run + every span (prompts, replies, usage, cost) |
| `GET` | `/admin/api/runs/{id}/files/{input.<ext> \| attempt-N.png}` | stored images |
| `POST` | `/admin/api/runs` | test run: `image`, `mode` (`mock`/`live`), `scenario` |
| `DELETE` | `/admin/api/runs/{id}` | delete a run, its spans and images |
| `PUT` | `/admin/api/settings` | `{"mode": "mock" \| "live"}` |
| `GET` | `/admin/api/scenarios` | mock scenarios |
