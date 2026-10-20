# Malameh headshot pipeline

Turns one casual phone photo into a professional LinkedIn headshot, keeping the face and any traditional attire (shemagh, hijab, niqab, thobe, abaya) exactly as they are.

```
upload ─▶ 1 Analyze ─▶ 2 Prompt ─▶ 3 Generate ─▶ 4 Validate ─▶ deliver
          (Claude)     (Claude)    (GPT Image)   (Claude)      │
                          ▲                                    ├─ retry: reviewer feedback goes back to 2 (max 3 attempts)
                          └────────────────────────────────────┘
                                                               └─ manual review: cultural gate failed / 3+ checks failed / attempts used up
```

## Behaviour guarantee

The pipeline's logic is the original one (`main` @ `fb4d30f`): the same four stages, prompts (byte for byte),
retry loop and reviewer feedback, thresholds (face 85, attire 90, background 95, realism 80), cultural gate,
manual-review rules, statuses, error messages, original API endpoints and CLI.

`tests/test_regression_legacy.py` proves it: it runs the frozen original code (`tests/legacy_reference/`) and
this code on the same scripted model replies (25 scenarios: every outcome, every error path, malformed replies)
and requires identical results, identical requests to Claude and the image API, and the same final image.

Everything added (tracing, costs, stores, dashboard, background endpoint, mock mode) runs *around* that logic.
Changes that *would* alter behaviour are listed in [docs/PROPOSED_CHANGES.md](docs/PROPOSED_CHANGES.md) and are
off until approved.

Every provider call is traced: the exact prompts, the response, token usage, cost and timing, plus the input
photo and every generated attempt.

## Live and mock

| | Live (default) | Mock (explicit, for tests and demos) |
|---|---|---|
| API calls | Claude + OpenAI | none |
| Cost | billed, recorded per call | $0, with an **estimate** of what live would cost |
| Stage 1/4 replies | Claude | fixtures in the original JSON shapes |
| Stage 3 output | GPT Image edit | the photo, cropped square, faded into the studio grey, stamped `MOCK` |
| Decisions | the original rules | the same original rules, on scripted scores |

Enable mock with `HEADSHOT_MODE=mock`, per command (`--mode mock`), or from the dashboard. The original
`python pipeline.py photo.jpg --mock` keeps its original meaning: real Claude stages, stand-in image.

Mock scenarios (`python -m headshot.cli scenarios`): `deliver`, `retry_then_deliver`, `manual_review`, `exhausted`,
`rejected`, `generation_retry`, `generation_error`. `auto` picks between the first two from the photo's hash.

## Run it

Needs Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # keys for live mode; ADMIN_TOKEN for the dashboard
```

```bash
.venv/bin/uvicorn api:app --port 8000
```

- API: see [API.md](API.md). The original `POST /v1/headshots` is unchanged; `/v1/runs` is the new background variant.
- Dashboard: `http://localhost:8000/admin` (asks for `ADMIN_TOKEN`)
- Interactive API docs: `http://localhost:8000/docs`

Command line (the original, unchanged):

```bash
.venv/bin/python pipeline.py photo.jpg                    # full pipeline (live)
.venv/bin/python pipeline.py photo.jpg --mock             # live Claude, stand-in image
.venv/bin/python pipeline.py photo.jpg --analyze-only     # stage 1 only
.venv/bin/python pipeline.py photo.jpg --stage 2          # stages 1-2
```

It writes the original output files to `output/` (`*_analysis.json`, `*_prompt.txt`, `*_headshot_vN.png`,
`*_validation_vN.json`, `*_prompt_retryN.txt`). `python -m stages.analyzer photo.jpg` and the other stage modules
work as before, and `from stages import PhotoAnalyzer, …` / `import config` still import.

More commands:

```bash
.venv/bin/python -m headshot.cli run photo.jpg --mode mock --scenario exhausted
.venv/bin/python -m headshot.cli runs
.venv/bin/python -m headshot.cli stats --days 30
.venv/bin/python -m headshot.cli check-models    # confirm both models exist for your keys (no tokens used)
.venv/bin/python -m headshot.cli check-store     # write/read/delete a test run in the trace store
```

Tests (offline, free):

```bash
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest
```

## Settings

All in `.env.example`. The ones you will touch:

| Setting | Purpose |
|---|---|
| `HEADSHOT_MODE` | `live` (default) or `mock` |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | live mode |
| `CLAUDE_MODEL` | default `claude-sonnet-5-5` (original: `claude-sonnet-4-20250514`, deprecated) |
| `IMAGE_MODEL` | default `gpt-image-2.5-sunburst` (original: `gpt-image-1`, shut down by OpenAI on 2026-10-23) |
| `IMAGE_SIZE`, `IMAGE_QUALITY` | original `1024x1024`, `medium` |
| `MAX_RETRIES` | original `3` |
| `REVIEW_FAIL_SAFE`, `IMAGE_SEND_INPUT_FIDELITY` | proposals P1 / P2, off until approved |
| `ADMIN_TOKEN` | protects `/admin` |
| `CORS_ORIGINS`, `CORS_ORIGIN_REGEX` | website origins allowed to call `/v1/runs` |
| `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | switch the trace store to Supabase |

## Deploy (Render)

`render.yaml` describes the API as one web service (free plan, Singapore, near the Supabase project).

1. Render dashboard → **New → Blueprint** → pick this repository and branch.
2. Fill the values it asks for: `HEADSHOT_MODE` (`live` for production, `mock` to try it without keys),
   `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and the two model keys for live mode.
   `ADMIN_TOKEN` is generated for you; read it under the service's **Environment** tab.
3. After the first deploy, check `https://<service>.onrender.com/health`, then open `/admin`.

The site's origin (`https://malameh-site.vercel.app`) and its Vercel preview URLs are allowed by
`CORS_ORIGINS` / `CORS_ORIGIN_REGEX`. Traces must use Supabase there: Render's disk does not survive restarts.
Apply database migrations from your machine with `./scripts/apply-migrations.sh` (needs `SUPABASE_DB_URL` in `.env`).

## Where traces go

- **SQLite** (when Supabase is not configured): `data/traces.sqlite3` and `data/runs/<run_id>/`. `data/` is git-ignored because it holds people's photos.
- **Supabase**: apply `supabase/migrations/*.sql`, then set `SUPABASE_URL` and `SUPABASE_SECRET_KEY`. Tables `runs`, `spans`, `settings` (RLS on, no policies: only the secret key can read them), a `trace_stats()` function for the dashboard, and a private `malameh-headshots` bucket.

What is stored:

- **runs**: mode, status, stage, attempts, input metadata, the Stage 1 analysis, the original result
  (`status`, `retries`, `error`, `prompt`, `validation`), a per-attempt review summary, `reason` (why it ended),
  total cost (billed or estimated), duration.
- **spans**: one per provider call: stage, attempt, provider, model, the full request (system prompt, user
  message, parameters; images by name), the reply, token usage, cost and timing, or the error.
- **files**: the upload exactly as received (`input.<ext>`) and every `attempt-N.png`.

Prices live in `headshot/pricing.py` with the date they were checked. A model without a price entry is still
traced; its cost shows as `—`.

## Layout

```
api.py, pipeline.py, config.py, stages/   original entry points and import paths (kept working)
headshot/
  settings.py           every setting, read from the environment (original defaults)
  pipeline.py           create_run / execute_run: the original orchestrator, plus tracing
  stages.py             the original four stages and threshold rule
  prompts.py            the original prompts, byte for byte
  providers/            claude.py, openai_images.py (live), mock.py
  tracing.py, pricing.py, imaging.py, mock_data.py, jobs.py, compat.py
  store/                sqlite.py, supabase.py
  api/                  original + new routes, dashboard (admin_ui/)
  cli.py
docs/PROPOSED_CHANGES.md  behaviour changes awaiting approval
supabase/migrations/      schema for the Supabase store
tests/                    offline tests; legacy_reference/ is the frozen original code
```
