"""
FastAPI application factory.

    uvicorn api:app --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..jobs import JobRunner
from ..settings import Settings
from ..store import TraceStore, build_store
from . import admin, public
from .common import AppState
from .ratelimit import RateLimiter


def create_app(settings: Settings | None = None, store: TraceStore | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or build_store(settings)
    jobs = JobRunner(settings, store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        jobs.recover()
        yield
        jobs.shutdown()

    app = FastAPI(
        title="Malameh Headshot API",
        description="Upload a portrait, follow the run, fetch the delivered headshot. Admin endpoints need ADMIN_TOKEN.",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.headshot = AppState(
        settings=settings,
        store=store,
        jobs=jobs,
        limiter=RateLimiter(settings.rate_limit_per_hour, 3600),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(public.router)
    app.include_router(admin.page)
    app.include_router(admin.api)
    app.mount("/admin/static", StaticFiles(directory=admin.UI_DIR), name="admin-static")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return app
