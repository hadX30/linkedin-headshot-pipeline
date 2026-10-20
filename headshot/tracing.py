"""
Per-run tracer. Every provider call runs inside `tracer.span(...)`, which
records the request, the response, token usage, cost and timing as one span,
and keeps the run's running total cost up to date.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import pricing
from .providers.base import CallResult
from .store import TraceStore, new_id, utcnow

log = logging.getLogger("headshot.tracing")


@dataclass
class SpanRecord:
    """Filled in by the caller inside the `with` block."""

    result: CallResult | None = None
    response: dict[str, Any] = field(default_factory=dict)


class Tracer:
    def __init__(self, store: TraceStore, run_id: str, mode: str):
        self.store = store
        self.run_id = run_id
        self.mode = mode
        self.seq = 0
        self.total_cost = 0.0
        self.unpriced_models: set[str] = set()

    @contextmanager
    def span(self, *, stage: str, attempt: int, provider: str, model: str, request: dict[str, Any]) -> Iterator[SpanRecord]:
        self.seq += 1
        record = SpanRecord()
        started_at = utcnow()
        start = time.perf_counter()
        status, error = "ok", None
        try:
            yield record
        except Exception as exc:
            status, error = "error", str(exc)
            raise
        finally:
            self._save(record, stage, attempt, provider, model, request, started_at, start, status, error)

    def _save(self, record, stage, attempt, provider, model, request, started_at, start, status, error) -> None:
        result = record.result
        usage = result.usage if result else {}
        model = result.model if result else model
        cost = pricing.cost_usd(model, usage) if usage else None
        if usage and cost is None:
            self.unpriced_models.add(model)

        response = dict(record.response)
        if result:
            response.setdefault("request_id", result.request_id)
            response.setdefault("stop_reason", result.stop_reason)
            if result.raw:
                response.setdefault("raw", result.raw)

        span = {
            "id": new_id(),
            "run_id": self.run_id,
            "seq": self.seq,
            "mode": self.mode,
            "stage": stage,
            "attempt": attempt,
            "provider": result.provider if result else provider,
            "model": model,
            "status": status,
            "started_at": started_at,
            "ended_at": utcnow(),
            "duration_ms": round((time.perf_counter() - start) * 1000),
            "request": request,
            "response": response or None,
            "usage": usage or None,
            "cost_usd": cost,
            "cost_estimated": self.mode == "mock",
            "error": error,
        }
        try:
            self.store.add_span(span)
            if cost:
                self.total_cost = round(self.total_cost + cost, 6)
                self.store.update_run(self.run_id, {"cost_usd": self.total_cost})
        except Exception:  # never let trace storage break the run itself
            log.exception("Could not save span %s for run %s", stage, self.run_id)
