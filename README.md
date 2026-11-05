# Malameh headshot pipeline

Turns one casual phone photo into a professional LinkedIn headshot, keeping the face and any traditional attire (shemagh, hijab, niqab, thobe, abaya) exactly as they are.

```
upload ─▶ 1 Analyze ─▶ 2 Prompt ─▶ 3 Generate ─▶ 4 Validate ─▶ deliver the selected candidate
          (Claude)     (Claude)    (GPT Image)   (Claude)      │
                          ▲                                    ├─ not deliverable: reviewer feedback goes back to 2,
                          └────────────────────────────────────┘  another candidate (up to 3); all are kept
                                                               └─ none deliverable after 3: the best one goes to manual review
```

## How a candidate is judged

Defined in `config.py`:

| Check | Weight | Hard floor | Ideal (reported, not required) |
|---|---|---|---|
| Face match | 35% | 78 | 85 |
| Attire | 30% | 80 | 90 |
| Realism | 20% | — | 80 |
| Background | 15% | 65 | 80 |
| Cultural check | — | must pass | — |

A candidate is **deliverable** when it clears every hard floor, passes the cultural check, and its weighted
score is at least **82**. The loop stops at the first deliverable candidate. Otherwise it generates another (up to 3),
and if none is deliverable, the best one goes to manual review. The reasoning and the measured evidence are in
[FINDINGS.md](FINDINGS.md) and the `40dbe16` commit message.

## Where things live

- **The pipeline:** `pipeline.py`, `stages/`, `config.py`. Owned by the team and run as written. Change
  behaviour there.
- **The apps:** `headshot/`. The web API, the admin dashboard, tracing, cost tracking, mock mode and the trace
  store. They contain no pipeline logic. `headshot/runner.py` calls `pipeline.run_pipeline()`, plugging in at the
  same place `tests/test_pipeline_integration.py` does. It swaps in the stage classes and the SDK clients
  to record each call and report progress, and leaves every decision to the pipeline.

`tests/test_pipeline_unchanged.py` proves the apps don't change the pipeline. It runs the pipeline alone, in a
fresh interpreter, and through the apps, on the same scripted model replies (11 scenarios: each outcome, error
path and edge case). Both runs must return the same result, send the same requests to Claude and the image API,
and end on the same image. Any change to the pipeline reaches the API, dashboard and website automatically.

Every model call is traced: the exact prompts, the reply, token usage, cost and timing. So is the input photo
and every candidate image.

## Live and mock

| | Live (default) | Mock (explicit, for tests and demos) |
|---|---|---|
| API calls | Claude + OpenAI | none |
| Cost | billed, recorded per call | $0, with an **estimate** of what live would cost |
| Model replies | Claude, GPT Image | scripted replies in the same shapes, and a local stand-in image stamped `MOCK` |
| Decisions | the pipeline's code | the same code, on the scripted scores |

Enable mock with `HEADSHOT_MODE=mock`, per command (`--mode mock`), or from the dashboard. `python pipeline.py
photo.jpg --mock` keeps its own meaning: real Claude stages, and the image stage copies the photo.

Mock scenarios (`python -m headshot.cli scenarios`) include `deliver`, `floor_then_deliver`,
`cultural_then_deliver`, `below_bar_then_deliver`, `no_deliverable`, `cultural_fail`, `rejected`,
`generation_failure`, `review_failure` and `generation_error`. `auto` picks between the first two from the
photo's hash.

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

- API: see [API.md](API.md). `POST /v1/headshots` runs the pipeline and returns its result; `/v1/runs` is the
  background variant the website uses.
- Dashboard: `http://localhost:8000/admin` (asks for `ADMIN_TOKEN`)
- Interactive API docs: `http://localhost:8000/docs`

The pipeline's own command line:

```bash
.venv/bin/python pipeline.py photo.jpg                    # full pipeline (live)
.venv/bin/python pipeline.py photo.jpg --mock             # live Claude; the image stage copies the photo
.venv/bin/python pipeline.py photo.jpg --analyze-only     # stage 1 only
.venv/bin/python pipeline.py photo.jpg --stage 2          # stages 1-2
```

It writes its output files to `output/`: `*_analysis.json`, `*_prompt.txt`, `*_headshot_vN.png` per candidate,
`*_validation_vN.json`, `*_prompt_retryN.txt`, and `*_headshot_FINAL.png` (the selected one). The team's tools
are in `tools/` (preflight, re-score a run, measure reviewer variance); they call the real APIs.

More commands:

```bash
.venv/bin/python -m headshot.cli run photo.jpg --mode mock --scenario no_deliverable   # traced, free
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

Environment settings are in `.env.example`; the decision rules are in `config.py`. The ones you will touch:

| Setting | Purpose |
|---|---|
| `HEADSHOT_MODE` | `live` (default) or `mock` |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | live mode |
| `CLAUDE_MODEL` | default `claude-sonnet-5-5` |
| `IMAGE_MODEL` | default `gpt-image-2.5-sunburst` |
| `IMAGE_SIZE`, `IMAGE_QUALITY` | default `1024x1024`, `medium` |
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

- **runs**: mode, status, stage, candidates generated, input metadata, the Stage 1 analysis, the pipeline's result
  (`status`, `selected_attempt`, `candidate_scores`, `validation`, `error`, …), a summary per candidate (scores,
  floors, weighted score, verdict, selected), `reason` (why it ended), total cost (billed or estimated), duration.
- **spans**: one per provider call: stage, attempt, provider, model, the full request (system prompt, user
  message, parameters; images by name), the reply, token usage, cost and timing, or the error.
- **files**: the upload exactly as received (`input.<ext>`) and every candidate as `attempt-N.png`.

Prices live in `headshot/pricing.py` with the date they were checked. A model without a price entry is still
traced; its cost shows as `—`.

## Layout

```
pipeline.py, config.py, stages/   the pipeline (the team's code); FINDINGS.md, tools/
api.py                            ASGI entry point (uvicorn api:app)
headshot/                         the apps around the pipeline
  runner.py             create_run / execute_run: runs pipeline.run_pipeline() with tracing and progress
  clients.py            traced Claude / GPT Image clients
  mock.py               mock scenarios and stand-in clients
  settings.py           environment settings (keys, models, mode, store, API)
  tracing.py, pricing.py, imaging.py, jobs.py
  store/                sqlite.py, supabase.py
  api/                  public routes, admin routes, dashboard (admin_ui/)
  cli.py
docs/PROPOSED_CHANGES.md  open proposals and what the pipeline has since adopted
supabase/migrations/      schema for the Supabase store
tests/                    offline tests (the team's, plus apps and the unchanged-pipeline proof)
```
