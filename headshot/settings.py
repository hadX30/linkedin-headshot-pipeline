"""
Runtime settings, read once from environment variables (and an optional .env file).

Every value has a safe default so the pipeline runs in mock mode with no keys,
no network and no cost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent

MODES = ("mock", "live")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Thresholds:
    """Minimum Stage 4 scores (0-100). Cultural sensitivity is a hard pass/fail gate."""

    face_match: int = 85
    attire_accuracy: int = 90
    background: int = 95
    realism: int = 80

    def as_dict(self) -> dict[str, int]:
        return {
            "face_match": self.face_match,
            "attire_accuracy": self.attire_accuracy,
            "background": self.background,
            "realism": self.realism,
        }


@dataclass(frozen=True)
class Settings:
    # ── Mode ────────────────────────────────────────────────────────────────
    # Default mode at startup. The admin dashboard can switch it at runtime.
    mode: str = "mock"

    # ── Live providers ──────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"
    claude_effort: str = ""  # "", "low", "medium", "high"; empty = model default
    claude_structured_outputs: bool = True
    image_model: str = "gpt-image-2.5-sunburst"
    image_size: str = "1024x1024"
    image_quality: str = "medium"
    image_input_fidelity: str = "high"  # only sent to models that accept it

    # ── Pipeline ────────────────────────────────────────────────────────────
    max_attempts: int = 3
    thresholds: Thresholds = field(default_factory=Thresholds)
    max_image_edge: int = 2048
    min_image_edge: int = 400

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
    cors_origin_regex: str = ""  # e.g. Vercel preview URLs: https://malameh-site(-[a-z0-9-]+)?\.vercel\.app
    max_upload_mb: int = 12
    rate_limit_per_hour: int = 20
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

        mode = _env("HEADSHOT_MODE", "mock").lower()
        if mode not in MODES:
            raise ValueError(f"HEADSHOT_MODE must be one of {MODES}, got {mode!r}")

        supabase_url = _env("SUPABASE_URL").rstrip("/")
        # New projects issue sb_secret_... keys; older ones a service_role JWT. Either works.
        supabase_key = _env("SUPABASE_SECRET_KEY") or _env("SUPABASE_SERVICE_ROLE_KEY")
        default_store = "supabase" if supabase_url and supabase_key else "sqlite"
        trace_store = _env("TRACE_STORE", default_store).lower()
        if trace_store not in ("sqlite", "supabase"):
            raise ValueError(f"TRACE_STORE must be 'sqlite' or 'supabase', got {trace_store!r}")

        data_dir = Path(_env("DATA_DIR") or ROOT_DIR / "data")
        if not data_dir.is_absolute():
            data_dir = ROOT_DIR / data_dir

        return cls(
            mode=mode,
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            openai_api_key=_env("OPENAI_API_KEY"),
            claude_model=_env("CLAUDE_MODEL", cls.claude_model),
            claude_effort=_env("CLAUDE_EFFORT").lower(),
            claude_structured_outputs=_env_bool("CLAUDE_STRUCTURED_OUTPUTS", True),
            image_model=_env("IMAGE_MODEL", cls.image_model),
            image_size=_env("IMAGE_SIZE", cls.image_size),
            image_quality=_env("IMAGE_QUALITY", cls.image_quality),
            image_input_fidelity=_env("IMAGE_INPUT_FIDELITY", cls.image_input_fidelity),
            max_attempts=_env_int("MAX_ATTEMPTS", cls.max_attempts),
            mock_scenario=_env("MOCK_SCENARIO", cls.mock_scenario),
            mock_latency_ms=_env_int("MOCK_LATENCY_MS", cls.mock_latency_ms),
            trace_store=trace_store,
            data_dir=data_dir,
            supabase_url=supabase_url,
            supabase_service_key=supabase_key,
            supabase_schema=_env("SUPABASE_SCHEMA", cls.supabase_schema),
            supabase_bucket=_env("SUPABASE_BUCKET", cls.supabase_bucket),
            admin_token=_env("ADMIN_TOKEN"),
            cors_origins=_env_list(
                "CORS_ORIGINS",
                ["http://localhost:5173", "http://127.0.0.1:5173"],
            ),
            cors_origin_regex=_env("CORS_ORIGIN_REGEX"),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", cls.max_upload_mb),
            rate_limit_per_hour=_env_int("RATE_LIMIT_PER_HOUR", cls.rate_limit_per_hour),
            workers=_env_int("WORKERS", cls.workers),
        )
