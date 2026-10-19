from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
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
from morning_intelligence.tts import AudioService


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


class SlowCountingProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()

    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult:
        del events, target_minutes, language, characters_per_minute
        self.calls += 1
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("slow provider should only exit through cancellation")


class FailingTTSProvider:
    name = "failing-fake"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def cache_identity(self) -> Mapping[str, str]:
        return {
            "provider": self.name,
            "voice": "test",
            "style": "newscast",
            "rate": "0%",
            "output_format": "test-mp3-v1",
        }

    async def synthesize(self, text: str, output_path: Path) -> None:
        del text, output_path
        self.calls += 1
        raise RuntimeError("simulated TTS outage")


def _service(
    path,
    provider: LLMProvider | None,
    *,
    failing_source: bool = False,
    generation_deadline_seconds: float = 180,
    audio_service: AudioService | None = None,
) -> MorningBriefingService:
    settings = Settings(
        _env_file=None,
        morning_database_path=path,
        morning_background_refresh_enabled=False,
        morning_min_llm_interval_hours=20,
        morning_generation_deadline_seconds=generation_deadline_seconds,
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
        audio_service=audio_service,
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


async def test_deadline_timeout_persists_failed_pipeline_status(tmp_path) -> None:
    path = tmp_path / "deadline-status.db"
    provider = SlowCountingProvider()
    service = _service(path, provider, generation_deadline_seconds=0.1)
    local_date = datetime.now(UTC).date()

    with pytest.raises(TimeoutError, match="generation_deadline_exceeded"):
        await service.deadline_daily_refresh(local_date)

    payload = _latest_pipeline_payload(path)
    assert payload["status"] == "failed"
    assert payload["status"] != "running"
    assert payload["fallback_reason"] == "generation_deadline_exceeded"
    assert "generation_deadline_exceeded" in payload["errors"]
    assert payload["total_latency_ms"] > 0


async def test_timeout_after_budget_claim_cannot_retry_provider(tmp_path) -> None:
    path = tmp_path / "deadline-budget.db"
    provider = SlowCountingProvider()
    service = _service(path, provider, generation_deadline_seconds=0.1)
    local_date = datetime.now(UTC).date()

    with pytest.raises(TimeoutError, match="generation_deadline_exceeded"):
        await service.deadline_daily_refresh(local_date)
    repeated = await service.deadline_daily_refresh(local_date)

    assert repeated.status == "already_claimed"
    assert provider.calls == 1
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT success, error, completed_at_utc FROM llm_daily_budget"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][:2] == (0, "generation_deadline_exceeded")
    assert rows[0][2] is not None


async def test_deadline_returns_prior_cache_without_replacing_it(tmp_path) -> None:
    path = tmp_path / "deadline-cache.db"
    provider = SlowCountingProvider()
    service = _service(path, provider, generation_deadline_seconds=0.1)
    cached = Briefing(
        event_count=1,
        estimated_duration_seconds=20,
        spoken_text="截止期限前已经存在的完整晨报",
    )
    service.repository.save_briefing(cached)

    result = await service.deadline_daily_refresh(datetime.now(UTC).date())

    assert result.status == "cached"
    assert result.budget_reason == "generation_deadline_exceeded"
    assert result.briefing is not None
    assert result.briefing.id == cached.id
    assert service.latest() is not None
    assert service.latest().id == cached.id  # type: ignore[union-attr]
    with sqlite3.connect(path) as connection:
        briefing_count = connection.execute("SELECT COUNT(*) FROM briefings").fetchone()[0]
    assert briefing_count == 1


async def test_external_cancellation_is_propagated(tmp_path) -> None:
    path = tmp_path / "external-cancellation.db"
    provider = SlowCountingProvider()
    service = _service(path, provider, generation_deadline_seconds=5)
    task = asyncio.create_task(service.deadline_daily_refresh(datetime.now(UTC).date()))
    await provider.started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    payload = _latest_pipeline_payload(path)
    assert payload["status"] == "failed"
    assert payload["fallback_reason"] == "refresh_cancelled"


async def test_successful_refresh_logs_stage_timings(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    provider = CountingProvider()
    service = _service(tmp_path / "stage-timings.db", provider)
    caplog.set_level(logging.INFO, logger="morning_intelligence.services.morning_briefing")

    result = await service.daily_refresh(datetime.now(UTC).date())

    assert result.status == "generated"
    assert provider.calls == 1
    timing_log = next(
        record.message
        for record in caplog.records
        if record.message.startswith("pipeline_stage_timings")
    )
    for field in (
        "source_collection_ms",
        "github_ms",
        "freshness_normalize_dedup_ms",
        "clustering_ms",
        "scoring_selection_ms",
        "llm_editorial_ms",
        "persistence_ms",
        "total_latency_ms",
    ):
        assert f"{field}=" in timing_log


async def test_manual_service_entry_cannot_bypass_daily_budget(tmp_path) -> None:
    provider = CountingProvider()
    service = _service(tmp_path / "manual-safe.db", provider)

    await service.generate(target_minutes=3, incremental_refresh=True, max_age_minutes=1)
    await service.generate(target_minutes=3, incremental_refresh=False, max_age_minutes=1)

    assert provider.calls == 1


async def test_background_tts_failure_does_not_change_refresh_or_llm_budget(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    llm_provider = CountingProvider()
    tts_provider = FailingTTSProvider()
    audio_service = AudioService(
        provider=tts_provider,
        audio_directory=tmp_path / "audio",
    )
    service = _service(
        tmp_path / "tts-isolation.db",
        llm_provider,
        audio_service=audio_service,
    )
    caplog.set_level(logging.WARNING)
    local_date = datetime.now(UTC).date()

    result = await service.daily_refresh(local_date)
    await asyncio.sleep(0)
    generated_payload = _latest_pipeline_payload(service.settings.morning_database_path)
    repeated = await service.daily_refresh(local_date)

    assert result.status == "generated"
    assert result.briefing is not None
    assert service.latest() is not None
    assert repeated.status == "already_claimed"
    assert llm_provider.calls == 1
    assert tts_provider.calls == 1
    assert generated_payload["status"] == "succeeded"
    assert generated_payload["llm_calls"] == 1
    with sqlite3.connect(service.settings.morning_database_path) as connection:
        budget_rows = connection.execute(
            "SELECT purpose, success FROM llm_daily_budget"
        ).fetchall()
    assert budget_rows == [("morning_editorial", 1)]
    assert any("background TTS generation failed" in record.message for record in caplog.records)


def _latest_pipeline_payload(path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        payload = connection.execute(
            "SELECT payload FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
    return json.loads(payload)  # type: ignore[no-any-return]
