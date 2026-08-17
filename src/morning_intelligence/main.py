from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from morning_intelligence.api import briefings_router, health_router
from morning_intelligence.config import Settings, get_settings
from morning_intelligence.llm import OpenAICompatibleProvider
from morning_intelligence.scheduler import refresh_loop
from morning_intelligence.services import CollectorService, MorningBriefingService
from morning_intelligence.sources import build_sources
from morning_intelligence.storage import SQLiteRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def build_service(settings: Settings) -> MorningBriefingService:
    preferences = settings.load_preferences()
    sources = build_sources(settings.load_sources())
    repository = SQLiteRepository(settings.morning_database_path)
    repository.initialize()
    collector = CollectorService(
        sources, timeout_seconds=settings.morning_source_timeout_seconds
    )
    provider = (
        OpenAICompatibleProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.morning_llm_timeout_seconds,
        )
        if settings.llm_api_key
        else None
    )
    return MorningBriefingService(
        settings=settings,
        preferences=preferences,
        repository=repository,
        collector=collector,
        llm_provider=provider,
    )


def create_app(
    *,
    settings: Settings | None = None,
    service: MorningBriefingService | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_service = service or build_service(runtime_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if runtime_settings.morning_background_refresh_enabled:
            task = asyncio.create_task(
                refresh_loop(
                    runtime_service,
                    runtime_settings.morning_refresh_interval_minutes,
                ),
                name="morning-intelligence-refresh",
            )
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    application = FastAPI(
        title="Morning Intelligence Harness",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.service = runtime_service
    application.include_router(health_router)
    application.include_router(briefings_router)
    return application


app = create_app()
