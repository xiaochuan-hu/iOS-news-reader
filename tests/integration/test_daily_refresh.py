from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from morning_intelligence.config import PreferencesConfig, Settings
from morning_intelligence.domain import (
    Briefing,
    EditorialBriefingOutput,
    EditorialBriefingResult,
    EventIntelligence,
    LLMUsage,
    NewsEvent,
    NewsItem,
    SourceType,
)
from morning_intelligence.llm import LLMProvider
from morning_intelligence.main import create_app
from morning_intelligence.services import CollectorService, MorningBriefingService
from morning_intelligence.storage import SQLiteRepository


class DailySource:
    name = "daily-source"

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]:
        return [
            NewsItem(
                source="Daily Source",
                source_type=SourceType.RSS,
                title="多个来源确认一项重要技术政策发生变化",
                url="https://example.com/policy",
                description="公开信息显示相关机构公布了新的技术政策安排。",
                published_at=datetime.now(UTC) - timedelta(minutes=5),
                category="technology",
                source_quality=0.95,
            )
        ]


class FailedSource:
    name = "failed-source"

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]:
        raise httpx.ConnectError("offline")


class CountingProvider(LLMProvider):
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult:
        self.calls += 1
        if self.fail:
            raise httpx.ReadTimeout("simulated timeout")
        sentence = "今天值得关注的政策变化已经由公开信息支持，后续影响仍要等待新的可靠进展。"
        return EditorialBriefingResult(
            output=EditorialBriefingOutput(
                events=[
                    EventIntelligence(
                        event_id=events[0].id,
                        summary_zh="相关机构公布了新的技术政策安排。",
                        why_it_matters="这可能影响相关参与者接下来的决策。",
                        watch_next="后续是否公布更具体的执行信息。",
                        key_facts=["相关机构已经公布安排"],
                        confidence=0.9,
                        source_consensus="输入来源的核心表述一致",
                    )
                ],
                spoken_text="早上好。" + sentence * 35,
            ),
            usage=LLMUsage(
                prompt_tokens=120,
                completion_tokens=240,
                total_tokens=360,
                cache_hit_tokens=20,
                cache_miss_tokens=100,
            ),
        )


class BlockingProvider(CountingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult:
        self.started.set()
        await self.release.wait()
        return await super().generate_editorial_briefing(
            events,
            target_minutes=target_minutes,
            language=language,
            characters_per_minute=characters_per_minute,
        )


def _service(
    path,
    provider: LLMProvider | None,
    *,
    failing_source: bool = False,
) -> MorningBriefingService:
    settings = Settings(
        _env_file=None,
        morning_database_path=path,
        morning_background_refresh_enabled=False,
        morning_min_llm_interval_hours=20,
        llm_model="fixture-model",
    )
    repository = SQLiteRepository(path)
    repository.initialize()
    source = FailedSource() if failing_source else DailySource()
    return MorningBriefingService(
        settings=settings,
        preferences=PreferencesConfig(),
        repository=repository,
        collector=CollectorService([source], timeout_seconds=1),  # type: ignore[list-item]
        llm_provider=provider,
    )


async def test_first_daily_post_and_100_duplicates_make_one_provider_call(tmp_path) -> None:
    provider = CountingProvider()
    service = _service(tmp_path / "daily.db", provider)
    now = datetime.now(UTC)
    local_date = now.date()

    first = await service.daily_refresh(local_date, now_utc=now)
    duplicates = [
        await service.daily_refresh(local_date, now_utc=now + timedelta(minutes=1))
        for _ in range(100)
    ]

    assert first.status == "generated"
    assert first.briefing is not None
    assert all(result.status == "already_claimed" for result in duplicates)
    assert provider.calls == 1
    assert service.latest() is not None


async def test_concurrent_services_share_one_sqlite_claim(tmp_path) -> None:
    path = tmp_path / "concurrent-service.db"
    first_provider = CountingProvider()
    second_provider = CountingProvider()
    first = _service(path, first_provider)
    second = _service(path, second_provider)
    now = datetime.now(UTC)

    results = await asyncio.gather(
        first.daily_refresh(now.date(), now_utc=now),
        second.daily_refresh(now.date(), now_utc=now),
    )

    assert sorted(result.status for result in results) == ["already_claimed", "generated"]
    assert first_provider.calls + second_provider.calls == 1


async def test_failed_llm_claim_survives_restart_and_is_not_retried(tmp_path) -> None:
    path = tmp_path / "failed-restart.db"
    now = datetime.now(UTC)
    failing = CountingProvider(fail=True)
    first_process = _service(path, failing)

    first = await first_process.daily_refresh(now.date(), now_utc=now)
    assert first.status == "generated_fallback"
    assert first.briefing is not None
    assert failing.calls == 1

    replacement = CountingProvider()
    second_process = _service(path, replacement)
    repeated = await second_process.daily_refresh(
        now.date(), now_utc=now + timedelta(hours=3)
    )

    assert repeated.status == "already_claimed"
    assert replacement.calls == 0
    assert second_process.latest() is not None


async def test_next_local_date_obeys_rolling_20_hour_gate(tmp_path) -> None:
    provider = CountingProvider()
    service = _service(tmp_path / "travel.db", provider)
    now = datetime.now(UTC)

    assert (await service.daily_refresh(now.date(), now_utc=now)).status == "generated"
    blocked = await service.daily_refresh(
        now.date() + timedelta(days=1), now_utc=now + timedelta(hours=19)
    )
    allowed = await service.daily_refresh(
        now.date() + timedelta(days=1), now_utc=now + timedelta(hours=20)
    )

    assert blocked.status == "already_claimed"
    assert blocked.budget_reason == "min_interval"
    assert allowed.status == "generated"
    assert provider.calls == 2


async def test_source_failure_before_claim_leaves_budget_available(tmp_path) -> None:
    path = tmp_path / "source-failure.db"
    provider = CountingProvider()
    now = datetime.now(UTC)
    failing_service = _service(path, provider, failing_source=True)

    try:
        await failing_service.daily_refresh(now.date(), now_utc=now)
    except RuntimeError as exc:
        assert "all configured news sources failed" in str(exc)
    else:
        raise AssertionError("an empty cache with all sources failed must be unavailable")
    assert provider.calls == 0

    working_service = _service(path, provider)
    result = await working_service.daily_refresh(
        now.date(), now_utc=now + timedelta(minutes=10)
    )
    assert result.status == "generated"
    assert provider.calls == 1


async def test_no_provider_generates_local_fallback_without_budget_claim(tmp_path) -> None:
    service = _service(tmp_path / "no-provider.db", None)
    now = datetime.now(UTC)

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.status == "no_llm_available"
    assert result.briefing is not None
    with sqlite3.connect(service.settings.morning_database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM llm_daily_budget").fetchone()[0]
    assert count == 0


def test_real_service_gets_100_times_each_without_provider_calls(tmp_path) -> None:
    provider = CountingProvider()
    service = _service(tmp_path / "cached-get.db", provider)
    cached = Briefing(
        event_count=1,
        estimated_duration_seconds=20,
        spoken_text="完全来自 SQLite 的缓存晨报",
    )
    service.repository.save_briefing(cached)
    client = TestClient(create_app(settings=service.settings, service=service))

    for _ in range(100):
        assert client.get("/v1/briefings/latest").status_code == 200
        response = client.get("/v1/briefings/latest/text")
        assert response.status_code == 200
        assert response.text == cached.spoken_text

    assert provider.calls == 0


def test_real_service_startup_with_empty_or_existing_db_never_calls_provider(
    tmp_path,
) -> None:
    empty_provider = CountingProvider()
    empty_service = _service(tmp_path / "empty-startup.db", empty_provider)
    with TestClient(create_app(settings=empty_service.settings, service=empty_service)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/briefings/latest").status_code == 503
    assert empty_provider.calls == 0

    cached_provider = CountingProvider()
    cached_service = _service(tmp_path / "cached-startup.db", cached_provider)
    cached_service.repository.save_briefing(
        Briefing(event_count=1, estimated_duration_seconds=10, spoken_text="缓存")
    )
    with TestClient(create_app(settings=cached_service.settings, service=cached_service)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/briefings/latest").status_code == 200
    assert cached_provider.calls == 0


async def test_old_cache_remains_readable_while_new_generation_is_in_flight(tmp_path) -> None:
    provider = BlockingProvider()
    service = _service(tmp_path / "atomic-cache.db", provider)
    old = Briefing(event_count=1, estimated_duration_seconds=10, spoken_text="昨天的完整缓存")
    service.repository.save_briefing(old)
    now = datetime.now(UTC)

    task = asyncio.create_task(service.daily_refresh(now.date(), now_utc=now))
    await provider.started.wait()
    during_generation = service.latest()
    assert during_generation is not None
    assert during_generation.id == old.id

    provider.release.set()
    result = await task
    assert result.status == "generated"
    assert service.latest() is not None
    assert service.latest().id != old.id  # type: ignore[union-attr]


async def test_manual_service_entry_cannot_bypass_daily_budget(tmp_path) -> None:
    provider = CountingProvider()
    service = _service(tmp_path / "manual-safe.db", provider)

    await service.generate(target_minutes=3, incremental_refresh=True, max_age_minutes=1)
    await service.generate(target_minutes=3, incremental_refresh=False, max_age_minutes=1)

    assert provider.calls == 1
