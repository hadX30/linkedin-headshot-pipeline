"""Pick the trace store from settings."""

from __future__ import annotations

from ..settings import Settings
from .base import FINISHED_STATUSES, RUN_STATUSES, STAGES, TraceStore, new_id, utcnow


def build_store(settings: Settings) -> TraceStore:
    if settings.trace_store == "supabase":
        if not settings.supabase_configured:
            raise RuntimeError("TRACE_STORE=supabase needs SUPABASE_URL and SUPABASE_SECRET_KEY.")
        from .supabase import SupabaseStore

        return SupabaseStore(
            settings.supabase_url,
            settings.supabase_service_key,
            schema=settings.supabase_schema,
            bucket=settings.supabase_bucket,
        )

    from .sqlite import SQLiteStore

    return SQLiteStore(settings.data_dir)


__all__ = ["FINISHED_STATUSES", "RUN_STATUSES", "STAGES", "TraceStore", "build_store", "new_id", "utcnow"]
