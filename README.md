# Malameh headshot pipeline

Turns one casual phone photo into a professional LinkedIn headshot, keeping the face and any traditional attire (shemagh, hijab, niqab, thobe, abaya) exactly as they are.

```
upload ─▶ 1 Analyze ─▶ 2 Prompt ─▶ 3 Generate ─▶ 4 Validate ─▶ deliver
          (Claude)     (Claude)    (GPT Image)   (Claude)      │
                          ▲                                    ├─ retry: feedback goes back to 2 (max 3 attempts)
                          └────────────────────────────────────┘
                                                               └─ manual review: cultural gate failed / too many failures
```

Every provider call is traced: the exact prompts, the response, token usage, cost and timing, plus the input photo and every generated attempt.

## Mock and live

| | Mock (default) | Live |
|---|---|---|
| API calls | none | Claude + OpenAI |
| Cost | $0, with an **estimate** of what live would cost | billed, recorded per call |
| Stage 1/4 output | fixtures (4 photo profiles) | Claude, as validated JSON |
| Stage 3 output | the photo, cropped square and faded into the studio grey, stamped `MOCK` | GPT Image edit |
| Outcome | a scripted scenario (see below) | whatever happens |

Switch with `HEADSHOT_MODE=mock|live`, or at runtime from the dashboard (the saved choice wins). Live mode refuses to start without both API keys.

Mock scenarios (`python -m headshot.cli scenarios`): `deliver`, `retry_then_deliver`, `manual_review`, `exhausted`, `rejected`, `generation_retry`, `generation_error`. `auto` (the default) picks between the first two from the photo's hash.

## Run it

Needs Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then set ADMIN_TOKEN (and keys only if you want live mode)
```

```bash
.venv/bin/uvicorn api:app --port 8000
```

- Website API: `http://localhost:8000/v1/...` (see [API.md](API.md))
- Dashboard: `http://localhost:8000/admin` (asks for `ADMIN_TOKEN`)
- Interactive API docs: `http://localhost:8000/docs`

Command line:

```bash
.venv/bin/python -m headshot.cli run photo.jpg                  # current mode
.venv/bin/python -m headshot.cli run photo.jpg --scenario exhausted
.venv/bin/python -m headshot.cli runs
.venv/bin/python -m headshot.cli stats --days 30
.venv/bin/python pipeline.py photo.jpg --mock                    # old entry point still works
```

Tests (offline, free):

```bash
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest
```

## Settings

All in `.env.example`. The ones you will touch:

| Setting | Purpose |
|---|---|
| `HEADSHOT_MODE` | `mock` (default) or `live` |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | live mode only |
| `CLAUDE_MODEL` | default `claude-sonnet-5-5` |
| `IMAGE_MODEL` | default `gpt-image-2.5-sunburst` (OpenAI shuts `gpt-image-1` down on 2026-10-23) |
| `IMAGE_QUALITY` | `low` / `medium` / `high` |
| `ADMIN_TOKEN` | protects `/admin` |
| `CORS_ORIGINS` | the website origins allowed to call the API |
| `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | switch the trace store to Supabase |

## Deploy (Render)

`render.yaml` describes the API as one web service (free plan, Singapore, near the Supabase project).

1. Render dashboard → **New → Blueprint** → pick this repository and branch.
2. Fill the values it asks for: `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (and the two model keys only if you want live mode).
   `ADMIN_TOKEN` is generated for you; read it under the service's **Environment** tab.
3. After the first deploy, check `https://<service>.onrender.com/health`, then open `/admin`.

The site's origin (`https://malameh-site.vercel.app`) and its Vercel preview URLs are already allowed by
`CORS_ORIGINS` / `CORS_ORIGIN_REGEX`. Traces must use Supabase there: Render's disk does not survive restarts.
Apply database migrations from your machine with `./scripts/apply-migrations.sh` (needs `SUPABASE_DB_URL` in `.env`).

## Where traces go

- **SQLite** (default): `data/traces.sqlite3` and `data/runs/<run_id>/` for images. Zero setup. `data/` is git-ignored because it holds people's photos.
- **Supabase**: apply `supabase/migrations/20261020120000_traces.sql` to a project, then set `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. Tables `runs`, `spans`, `settings` (RLS on, no policies: only the secret key can read them), a `trace_stats()` function for the dashboard, and a private `malameh-headshots` bucket.

What is stored:

- **runs**: mode, scenario, status, current stage, attempts, input photo metadata, Stage 1 analysis, every review, total cost (billed or estimated), duration.
- **spans**: one per provider call: stage, attempt, provider, model, the full request (system prompt, user text, parameters; images by name), the response (text or parsed JSON, request id, stop reason), token usage, cost and timing, or the error.
- **files**: `input.jpg` (normalised upload) and `attempt-N.png`.

Prices live in `headshot/pricing.py` with the date they were checked. A model without a price entry is still traced; its cost shows as `—`.

## Layout

```
api.py                  ASGI entry (uvicorn api:app)
pipeline.py             old CLI entry, forwards to headshot.cli
headshot/
  settings.py           every setting, read from the environment
  pipeline.py           create_run / execute_run: the orchestrator
  stages.py             the four stages + the deliver/retry/manual-review rule
  prompts.py            all system prompts and the v3 edit template
  schemas.py            typed Stage 1 and Stage 4 outputs
  tracing.py            one span per provider call
  pricing.py            per-model prices and cost maths
  imaging.py            upload validation/normalising, mock image
  mock_data.py          mock profiles and scenarios
  providers/            claude.py, openai_images.py (live), mock.py
  store/                sqlite.py, supabase.py
  jobs.py               background workers
  api/                  public + admin routes, dashboard (admin_ui/)
  cli.py
supabase/migrations/    schema for the Supabase store
tests/                  offline tests (no keys, no network)
```
