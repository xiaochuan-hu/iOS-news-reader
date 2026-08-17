from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from morning_intelligence.config import PreferencesConfig, Settings
from morning_intelligence.domain import NewsEvent, NewsItem, SourceType
from morning_intelligence.llm import LLMProvider
from morning_intelligence.services import CollectorService, MorningBriefingService
from morning_intelligence.storage import SQLiteRepository


class WorkingSource:
    name = "working"

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]:
        return [
            NewsItem(
                source="working",
                source_type=SourceType.RSS,
                title="人工智能公司发布经过验证的新模型",
                url="https://example.com/model",
                description="该公司公布了面向开发者的新模型和测试信息",
                published_at=datetime.now(UTC) - timedelta(minutes=10),
                category="technology",
                source_quality=0.8,
            )
        ]


class FailingSource:
    name = "failing"

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]:
        raise httpx.ConnectError("offline")


class FailingLLM(LLMProvider):
    async def score_events(self, events: list[NewsEvent]) -> dict[str, float]:
        raise TimeoutError("LLM timed out")

    async def summarize_events(self, events: list[NewsEvent]) -> dict[str, tuple[str, str]]:
        raise TimeoutError("LLM timed out")

    async def create_spoken_script(
        self, events: list[NewsEvent], *, target_minutes: int, language: str
    ) -> str:
        raise TimeoutError("LLM timed out")


async def test_partial_source_and_llm_failure_still_returns_briefing(tmp_path) -> None:
    settings = Settings(
        morning_database_path=tmp_path / "service.db",
        morning_background_refresh_enabled=False,
        morning_source_timeout_seconds=1,
    )
    repository = SQLiteRepository(settings.morning_database_path)
    repository.initialize()
    service = MorningBriefingService(
        settings=settings,
        preferences=PreferencesConfig(),
        repository=repository,
        collector=CollectorService([WorkingSource(), FailingSource()], timeout_seconds=1),  # type: ignore[list-item]
        llm_provider=FailingLLM(),
    )

    briefing = await service.refresh()
    assert briefing.event_count == 1
    assert briefing.spoken_text.startswith("早上好")
    assert repository.get_latest_briefing() is not None


async def test_total_refresh_failure_returns_cached_briefing(tmp_path) -> None:
    settings = Settings(
        morning_database_path=tmp_path / "fallback.db",
        morning_background_refresh_enabled=False,
        morning_source_timeout_seconds=1,
    )
    repository = SQLiteRepository(settings.morning_database_path)
    repository.initialize()
    cached = (await _cached_briefing())[0]
    repository.save_briefing(cached)
    service = MorningBriefingService(
        settings=settings,
        preferences=PreferencesConfig(),
        repository=repository,
        collector=CollectorService([FailingSource()], timeout_seconds=1),  # type: ignore[list-item]
        llm_provider=None,
    )
    result = await service.refresh()
    assert result.spoken_text == cached.spoken_text
    assert result.is_fallback is True


async def _cached_briefing():
    from morning_intelligence.briefing import BriefingGenerator

    return await BriefingGenerator().generate([], target_minutes=5, language="zh-CN")
