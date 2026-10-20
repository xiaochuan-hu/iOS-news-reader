from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from morning_intelligence.api import (
    briefings_router,
    health_router,
    media_router,
    podcast_router,
)
from morning_intelligence.config import Settings, get_settings
from morning_intelligence.llm import OpenAICompatibleProvider
from morning_intelligence.providers import GitHubTrendingProvider
from morning_intelligence.scheduler import refresh_loop
from morning_intelligence.services import CollectorService, MorningBriefingService
from morning_intelligence.sources import build_sources
from morning_intelligence.storage import SQLiteRepository
from morning_intelligence.tts import AudioService, AzureSpeechTTSProvider, MediaURLSigner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def build_audio_service(settings: Settings) -> AudioService:
    provider = None
    if (
        settings.morning_tts_enabled
        and settings.morning_tts_provider == "azure"
        and settings.azure_speech_key
        and settings.azure_speech_region
    ):
        provider = AzureSpeechTTSProvider(
            key=settings.azure_speech_key,
            region=settings.azure_speech_region,
            voice=settings.morning_tts_voice,
            style=settings.morning_tts_style,
            rate=settings.morning_tts_rate,
            timeout_seconds=settings.morning_tts_timeout_seconds,
        )
    elif settings.morning_tts_enabled:
        logging.getLogger(__name__).warning(
            "TTS is enabled but Azure Speech credentials are incomplete; audio is unavailable"
        )
    return AudioService(
        provider=provider,
        audio_directory=settings.morning_audio_directory,
        retention_days=settings.morning_audio_retention_days,
    )


def build_service(
    settings: Settings, *, audio_service: AudioService | None = None
) -> MorningBriefingService:
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
            thinking_mode=settings.llm_thinking_mode,
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
        github_provider=GitHubTrendingProvider(
            timeout_seconds=settings.morning_auxiliary_timeout_seconds
        ),
        audio_service=audio_service,
    )


def create_app(
    *,
    settings: Settings | None = None,
    service: MorningBriefingService | None = None,
    audio_service: AudioService | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_audio_service = audio_service or build_audio_service(runtime_settings)
    runtime_service = service or build_service(
        runtime_settings, audio_service=runtime_audio_service
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if (
            runtime_settings.morning_generation_trigger == "server"
            and runtime_settings.morning_background_refresh_enabled
        ):
            task = asyncio.create_task(
                refresh_loop(
                    runtime_service,
                    runtime_settings.morning_refresh_timezone,
                    runtime_settings.morning_refresh_time_local,
                ),
                name="morning-intelligence-refresh",
            )
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await runtime_audio_service.shutdown()

    application = FastAPI(
        title="Morning Intelligence Harness",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.service = runtime_service
    application.state.audio_service = runtime_audio_service
    application.state.media_signer = MediaURLSigner(
        runtime_settings.morning_media_signing_key
    )
    application.include_router(health_router)
    application.include_router(briefings_router)
    application.include_router(media_router)
    application.include_router(podcast_router)
    return application


app = create_app()
