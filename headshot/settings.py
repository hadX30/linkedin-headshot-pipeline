"""
Runtime settings, read once from environment variables (and an optional .env file).

The pipeline's decision rules (thresholds, hard floors, composite weights, delivery
bar, number of candidates) live in config.py with the pipeline. This module holds
what the environment decides: API keys, model names, generation parameters, the
output folder, and everything the apps need (mode, trace store, API, admin).
Live API calls are the default; mock mode must be enabled explicitly
(HEADSHOT_MODE=mock).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent

MODES = ("live", "mock")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_generation_params() -> dict:
    # config.GENERATION_PARAMS. stages/image_generator.py sends size, quality, n and
    # input_fidelity (dropping input_fidelity when a model rejects it).
    return {"size": "1024x1024", "quality": "medium", "output_format": "png", "input_fidelity": "high", "n": 1}


@dataclass(frozen=True)
class Settings:
    # ── Mode ────────────────────────────────────────────────────────────────
    # live = real API calls (the production default).
    # mock = no API calls at all, for tests and demos; must be chosen explicitly.
    mode: str = "live"

    # ── Read by config.py for the pipeline ──────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    image_model: str = "gpt-image-2.5-sunburst"
    generation_params: dict = field(default_factory=_default_generation_params)
    output_dir: Path = ROOT_DIR / "output"  # where pipeline.py writes its output files

    # ── Mock mode ───────────────────────────────────────────────────────────
    mock_scenario: str = "auto"
    mock_latency_ms: int = 700

    # ── Trace store ─────────────────────────────────────────────────────────
    trace_store: str = "sqlite"  # "sqlite" or "supabase"
    data_dir: Path = ROOT_DIR / "data"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_schema: str = "public"
    supabase_bucket: str = "malameh-headshots"

    # ── API ─────────────────────────────────────────────────────────────────
    admin_token: str = ""
    cors_origins: list[str] = field(default_factory=list)
    cors_origin_regex: str = ""  # e.g. Vercel previews: https://malameh-site(-[a-z0-9-]+)?\.vercel\.app
    max_upload_mb: int = 12        # applies to /v1/runs only
    rate_limit_per_hour: int = 20  # applies to /v1/runs only
    workers: int = 2

    @property
    def live_available(self) -> bool:
        return bool(self.anthropic_api_key and self.openai_api_key)

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        load_dotenv(env_file or ROOT_DIR / ".env")

        mode = _env("HEADSHOT_MODE", "live").lower()
        if mode not in MODES:
            raise ValueError(f"HEADSHOT_MODE must be one of {MODES}, got {mode!r}")

        supabase_url = _env("SUPABASE_URL").rstrip("/")
        # New projects issue sb_secret_... keys; older ones a service_role JWT. Either works.
        supabase_key = _env("SUPABASE_SECRET_KEY") or _env("SUPABASE_SERVICE_ROLE_KEY")
        default_store = "supabase" if supabase_url and supabase_key else "sqlite"
        trace_store = _env("TRACE_STORE", default_store).lower()
        if trace_store not in ("sqlite", "supabase"):
            raise ValueError(f"TRACE_STORE must be 'sqlite' or 'supabase', got {trace_store!r}")

        def path(name: str, default: Path) -> Path:
            value = Path(_env(name) or default)
            return value if value.is_absolute() else ROOT_DIR / value

        params = _default_generation_params()
        params["size"] = _env("IMAGE_SIZE", params["size"])
        params["quality"] = _env("IMAGE_QUALITY", params["quality"])

        return cls(
            mode=mode,
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            openai_api_key=_env("OPENAI_API_KEY"),
            claude_model=_env("CLAUDE_MODEL", cls.claude_model),
            image_model=_env("IMAGE_MODEL", cls.image_model),
            generation_params=params,
            output_dir=path("OUTPUT_DIR", ROOT_DIR / "output"),
            mock_scenario=_env("MOCK_SCENARIO", cls.mock_scenario),
            mock_latency_ms=_env_int("MOCK_LATENCY_MS", cls.mock_latency_ms),
            trace_store=trace_store,
            data_dir=path("DATA_DIR", ROOT_DIR / "data"),
            supabase_url=supabase_url,
            supabase_service_key=supabase_key,
            supabase_schema=_env("SUPABASE_SCHEMA", cls.supabase_schema),
            supabase_bucket=_env("SUPABASE_BUCKET", cls.supabase_bucket),
            admin_token=_env("ADMIN_TOKEN"),
            cors_origins=_env_list("CORS_ORIGINS", ["http://localhost:5173", "http://127.0.0.1:5173"]),
            cors_origin_regex=_env("CORS_ORIGIN_REGEX"),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", cls.max_upload_mb),
            rate_limit_per_hour=_env_int("RATE_LIMIT_PER_HOUR", cls.rate_limit_per_hour),
            workers=_env_int("WORKERS", cls.workers),
        )
