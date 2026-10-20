# Web API

Start: `.venv/bin/uvicorn api:app --port 8000`. Interactive docs: `/docs`.

Runs are asynchronous: the upload returns at once with a `run_id`, and the
website polls the run until it reaches a final state.

## Public (called by the website)

The browser origin must be listed in `CORS_ORIGINS`. Uploads are limited to
`MAX_UPLOAD_MB` and `RATE_LIMIT_PER_HOUR` per client IP.

### `GET /v1/config`

```json
{ "mode": "mock", "max_upload_mb": 12, "max_attempts": 3,
  "thresholds": { "face_match": 85, "attire_accuracy": 90, "background": 95, "realism": 80 } }
```

### `POST /v1/runs`

Multipart form: `image` (JPEG, PNG or WebP; the real file type is checked) and,
in mock mode only, an optional `scenario`.

`202` → `{ "run_id": "…", "status": "queued", "mode": "mock", "poll_url": "/v1/runs/…" }`

Errors: `413` too large · `415` not a supported image · `422` smaller than 400 px ·
`429` rate limited (`Retry-After` header) · `503` live mode without keys.

### `GET /v1/runs/{run_id}`

Poll every 1–2 s until `status` is final.

```json
{
  "id": "…", "status": "running", "stage": "generate", "mode": "mock", "attempts": 1,
  "reason": null,
  "analysis": {
    "headwear": { "type": "shemagh_with_iqal", "description_ar": "شماغ أحمر وأبيض بعقال أسود" },
    "clothing": { "type": "thobe", "is_professional": true, "description_ar": "ثوب أبيض" },
    "needs_wardrobe_change": false,
    "facial_hair": { "present": true, "description_ar": "…" },
    "glasses": { "present": false, "description_ar": "" },
    "photo_quality": { "face_angle": "frontal", "lighting_quality": "natural_poor", "background_type": "indoor", "has_beauty_filters": false },
    "decision": { "proceed": true, "mode": "full_edit", "reject_reason": "none" }
  },
  "reviews": [
    { "attempt": 1, "action": "retry", "reason": "checks_failed",
      "checks": { "face_match": { "score": 82, "threshold": 85, "passed": false, "issues_ar": ["…"] }, "…": {} },
      "cultural": { "passed": true, "issues_ar": [] } }
  ],
  "image_url": null,
  "error": null
}
```

| `status` | meaning | `reason` |
|---|---|---|
| `queued`, `running` | in progress; `stage` is `analyze` / `prompt` / `generate` / `validate` | |
| `delivered` | passed every check; `image_url` is set | `passed` |
| `manual_review` | needs a person; no public image | `cultural`, `multiple_checks`, `max_attempts` |
| `rejected` | photo not usable | `multiple_faces`, `no_face`, `low_resolution`, `minor_detected` |
| `error` | processing failed | |

The public view never includes prompts, costs, raw model output, or the
apparent gender / age / skin tone from the analysis.

### `GET /v1/runs/{run_id}/image`

The PNG, only when the run is `delivered`; otherwise `404`.

## Admin (dashboard and tooling)

All need `Authorization: Bearer <ADMIN_TOKEN>`. Without `ADMIN_TOKEN` set, they return `503`.

| Method | Path | |
|---|---|---|
| `GET` | `/admin` | the dashboard page |
| `GET` | `/admin/api/overview?days=30` | mode, models, thresholds, cost and outcome stats |
| `GET` | `/admin/api/runs?limit&offset&status&mode` | run list |
| `GET` | `/admin/api/runs/{id}` | run + every span (prompts, responses, usage, cost) |
| `GET` | `/admin/api/runs/{id}/files/{input.jpg \| attempt-N.png}` | stored images, including failed attempts |
| `POST` | `/admin/api/runs` | test run: `image`, `mode` (`mock`/`live`), `scenario` |
| `DELETE` | `/admin/api/runs/{id}` | delete a run, its spans and images |
| `PUT` | `/admin/api/settings` | `{"mode": "mock" \| "live"}`: the switch |
| `GET` | `/admin/api/scenarios` | mock scenarios |
| `GET` | `/health` | `{"status":"ok","mode":"mock","store":"sqlite"}` |
