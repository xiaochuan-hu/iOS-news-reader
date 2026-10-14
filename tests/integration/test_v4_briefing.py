from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from morning_intelligence.api.schemas import DailyRefreshRequest
from morning_intelligence.config import PreferencesConfig, Settings
from morning_intelligence.domain import (
    EditorialBriefingOutput,
    EditorialBriefingResult,
    EventIntelligence,
    GitHubRepositoryIntelligence,
    GitHubTrendingRepo,
    NewsEvent,
    NewsItem,
    SourceType,
)
from morning_intelligence.llm import LLMProvider
from morning_intelligence.main import create_app
from morning_intelligence.services import CollectorService, MorningBriefingService
from morning_intelligence.storage import SQLiteRepository


class MixedFreshnessSource:
    name = "mixed-v4-fixture"

    def __init__(self, now: datetime) -> None:
        self.now = now

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]:
        del since, client  # Deliberately ignore `since` to exercise the service hard filter.
        return [
            self._item("Global policy event", "world", 1),
            self._item("New foundation model inference system", "ai_llm_agent", 2),
            self._item("Embodied AI robot manipulation milestone", "embodied_ai", 3),
            self._item("Stale robotics item must never reach LLM", "robotics", 25),
        ]

    def _item(self, title: str, category: str, age_hours: int) -> NewsItem:
        return NewsItem(
            source="V4 fixture",
            source_type=SourceType.RSS,
            title=title,
            url=f"https://example.com/{category}/{age_hours}",
            description=f"Verified description for {title}.",
            published_at=self.now - timedelta(hours=age_hours),
            category=category,
            source_quality=0.99,
        )


class FixtureGitHubProvider:
    calls = 0

    async def fetch_today(self) -> list[GitHubTrendingRepo]:
        self.calls += 1
        return [
            GitHubTrendingRepo(
                rank=rank,
                full_name=f"owner/repo-{rank:02d}",
                url=f"https://github.com/owner/repo-{rank:02d}",
                description=f"Repository {rank} description",
                language="Python",
                stars_today=1000 - rank,
            )
            for rank in range(1, 11)
        ]


class FailingGitHubProvider:
    async def fetch_today(self) -> list[GitHubTrendingRepo]:
        raise httpx.ConnectError("fixture GitHub unavailable")


class StaticGitHubProvider:
    def __init__(
        self,
        repositories: list[GitHubTrendingRepo] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.repositories = repositories or []
        self.error = error

    async def fetch_today(self) -> list[GitHubTrendingRepo]:
        if self.error is not None:
            raise self.error
        return self.repositories


class V4RecordingLLM(LLMProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.events: list[NewsEvent] = []
        self.repositories: list[GitHubTrendingRepo] = []

    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
        github_repositories: list[GitHubTrendingRepo] | None = None,
    ) -> EditorialBriefingResult:
        del target_minutes, language, characters_per_minute
        self.calls += 1
        self.events = events
        self.repositories = github_repositories or []
        news = "过去二十四小时的重要新闻已经过时间窗口和来源证据核验。" * 30
        return EditorialBriefingResult(
            output=EditorialBriefingOutput(
                events=[
                    EventIntelligence(event_id=event.id, summary_zh=event.canonical_title)
                    for event in events
                ],
                news_spoken_text=news,
                github_repositories=[
                    GitHubRepositoryIntelligence(
                        repo=repo.full_name,
                        summary_zh=repo.description,
                        why_it_matters="今天的社区关注度值得继续观察",
                    )
                    for repo in self.repositories
                    if repo.rank != 7
                ],
            )
        )


async def test_complete_v4_pipeline_is_fresh_one_call_ordered_and_weather_free(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "v4.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    llm = V4RecordingLLM()
    github = FixtureGitHubProvider()
    service = MorningBriefingService(
        settings=Settings(
            _env_file=None,
            morning_database_path=path,
            morning_min_llm_interval_hours=20,
            llm_model="fixture",
        ),
        preferences=PreferencesConfig(),
        repository=repository,
        collector=CollectorService([MixedFreshnessSource(now)], timeout_seconds=1),  # type: ignore[list-item]
        llm_provider=llm,
        github_provider=github,  # type: ignore[arg-type]
    )

    result = await service.daily_refresh(now.date(), now_utc=now)
    duplicate = await service.daily_refresh(
        now.date(), now_utc=now + timedelta(minutes=1)
    )

    assert result.status == "generated"
    assert duplicate.status == "already_claimed"
    assert llm.calls == 1
    assert all(
        item.published_at is not None and item.published_at >= now - timedelta(hours=24)
        for event in llm.events
        for item in event.items
    )
    assert all("Stale robotics" not in event.canonical_title for event in llm.events)
    assert {category for event in llm.events for category in event.categories} >= {
        "world",
        "ai_llm_agent",
        "embodied_ai",
    }
    assert [repo.rank for repo in llm.repositories] == list(range(1, 11))
    assert result.briefing is not None
    spoken = result.briefing.spoken_text
    assert spoken.startswith("早上好。今天是2026年8月20日，星期四。")
    positions = [spoken.index(f"owner/repo-{rank:02d}") for rank in range(1, 11)]
    assert positions == sorted(positions)
    assert all(spoken.count(f"owner/repo-{rank:02d}") == 1 for rank in range(1, 11))
    assert "第七名，owner/repo-07" in spoken
    assert "它是Repository 7 description" in spoken
    assert spoken.endswith("以上就是今天 GitHub 热榜前十中值得关注的项目。")
    assert "天气" not in spoken

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM github_trending_snapshots WHERE snapshot_date = ?",
            (now.date().isoformat(),),
        ).fetchone()[0]
        metrics_payload = connection.execute(
            "SELECT payload FROM pipeline_runs ORDER BY started_at LIMIT 1"
        ).fetchone()[0]
    metrics = json.loads(metrics_payload)
    assert snapshot_count == 10
    assert metrics["items_rejected_stale"] == 1
    assert metrics["fresh_items_count"] == 3
    assert metrics["github_trending_count"] == 10
    assert metrics["github_fetch_success"] is True
    assert metrics["github_snapshot_date"] == now.date().isoformat()
    assert all("weather" not in table.casefold() for table in tables)

    client = TestClient(create_app(settings=service.settings, service=service))
    for _ in range(100):
        assert client.get("/v1/briefings/latest").status_code == 200
        response = client.get("/v1/briefings/latest/text")
        assert response.status_code == 200
        assert response.text == spoken
    assert llm.calls == 1


async def test_github_failure_uses_recent_complete_snapshot(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "github-fallback.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    cached = await FixtureGitHubProvider().fetch_today()
    repository.save_github_trending_snapshot(
        now.date(), cached, fetched_at_utc=now - timedelta(minutes=5)
    )
    service = MorningBriefingService(
        settings=Settings(_env_file=None, morning_database_path=path),
        preferences=PreferencesConfig(),
        repository=repository,
        collector=CollectorService([MixedFreshnessSource(now)], timeout_seconds=1),  # type: ignore[list-item]
        llm_provider=None,
        github_provider=FailingGitHubProvider(),  # type: ignore[arg-type]
    )

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert result.briefing.github_trending_count == 10
    assert all(
        result.briefing.spoken_text.count(repository.full_name) == 1
        for repository in cached
    )
    assert "最近一次成功获取的榜单" in result.briefing.spoken_text


async def test_seven_live_github_repositories_are_used_without_snapshot(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "github-live-partial.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    service = _service(
        path,
        repository,
        now,
        StaticGitHubProvider(_github_repositories(7, prefix="live")),
    )

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert result.briefing.github_trending_count == 7
    assert "实时榜单只获取到部分项目" in result.briefing.spoken_text
    assert _snapshot_row_count(repository, now.date()) == 0
    metrics = _latest_pipeline_metrics(repository)
    assert metrics["github_fetch_success"] is True
    assert metrics["github_trending_count"] == 7
    assert metrics["github_snapshot_date"] == now.date().isoformat()


async def test_four_live_github_repositories_are_used_without_snapshot(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "github-small-live-partial.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    service = _service(
        path,
        repository,
        now,
        StaticGitHubProvider(_github_repositories(4, prefix="live")),
    )
    caplog.set_level(logging.INFO, logger="morning_intelligence.services.morning_briefing")

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert result.briefing.github_trending_count == 4
    assert all(
        result.briefing.spoken_text.count(f"live/repo-{rank}") == 1
        for rank in range(1, 5)
    )
    assert "实时榜单只获取到部分项目" in result.briefing.spoken_text
    assert "source=live_partial" in caplog.text
    metrics = _latest_pipeline_metrics(repository)
    assert metrics["status"] == "succeeded"
    assert metrics["github_trending_count"] == 4
    assert metrics["github_fetch_success"] is True
    assert metrics["github_snapshot_date"] == now.date().isoformat()


async def test_two_local_date_old_complete_snapshot_is_accepted(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "github-two-day-boundary.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    snapshot_date = now.date() - timedelta(days=2)
    cached = _github_repositories(10, prefix="cached")
    repository.save_github_trending_snapshot(
        snapshot_date, cached, fetched_at_utc=now - timedelta(days=2)
    )
    service = _service(
        path,
        repository,
        now,
        StaticGitHubProvider(error=httpx.ConnectError("fixture failure")),
    )

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert result.briefing.github_trending_count == 10
    assert "最近一次成功获取的榜单" in result.briefing.spoken_text
    assert "最近一次可用的 GitHub 热榜前十" in result.briefing.spoken_text
    metrics = _latest_pipeline_metrics(repository)
    assert metrics["status"] == "succeeded"
    assert metrics["github_fetch_success"] is False
    assert metrics["github_snapshot_date"] == snapshot_date.isoformat()


@pytest.mark.parametrize(
    ("live_count", "error", "fetch_succeeded"),
    [
        (4, None, True),
        (0, None, False),
        (0, httpx.ConnectError("fixture failure"), False),
    ],
)
async def test_small_missing_or_failed_live_result_uses_recent_complete_snapshot(
    tmp_path: Path,
    live_count: int,
    error: Exception | None,
    fetch_succeeded: bool,
) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / f"github-cached-{live_count}-{type(error).__name__}.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    snapshot_date = now.date() - timedelta(days=1)
    cached = _github_repositories(10, prefix="cached")
    repository.save_github_trending_snapshot(
        snapshot_date, cached, fetched_at_utc=now - timedelta(days=1)
    )
    service = _service(
        path,
        repository,
        now,
        StaticGitHubProvider(
            _github_repositories(live_count, prefix="live"), error=error
        ),
    )

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert result.briefing.github_trending_count == 10
    assert "最近一次成功获取的榜单" in result.briefing.spoken_text
    assert "cached/repo-1" in result.briefing.spoken_text
    assert "live/repo-1" not in result.briefing.spoken_text
    metrics = _latest_pipeline_metrics(repository)
    assert metrics["status"] == "succeeded"
    assert metrics["github_fetch_success"] is fetch_succeeded
    assert metrics["github_trending_count"] == 10
    assert metrics["github_snapshot_date"] == snapshot_date.isoformat()


async def test_incomplete_recent_snapshot_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "github-incomplete-cache.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    incomplete = _github_repositories(10, prefix="cached")
    incomplete[-1] = incomplete[-1].model_copy(
        update={"full_name": incomplete[0].full_name, "url": incomplete[0].url}
    )
    repository.save_github_trending_snapshot(
        now.date(), incomplete, fetched_at_utc=now - timedelta(minutes=5)
    )
    service = _service(path, repository, now, StaticGitHubProvider())

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert result.briefing.github_trending_count == 0
    assert "实时榜单未能成功获取" in result.briefing.spoken_text


async def test_snapshot_older_than_two_local_dates_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "github-old-cache.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    old_date = now.date() - timedelta(days=3)
    repository.save_github_trending_snapshot(
        old_date,
        _github_repositories(10, prefix="old"),
        fetched_at_utc=now - timedelta(days=3),
    )
    service = _service(path, repository, now, StaticGitHubProvider())

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert result.briefing.github_trending_count == 0
    assert "实时榜单未能成功获取" in result.briefing.spoken_text
    assert "old/repo-1" not in result.briefing.spoken_text


async def test_no_live_or_cached_github_result_uses_unavailable_wording(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 20, 5, tzinfo=UTC)
    path = tmp_path / "github-unavailable.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    service = _service(path, repository, now, StaticGitHubProvider())

    result = await service.daily_refresh(now.date(), now_utc=now)

    assert result.briefing is not None
    assert "今天 GitHub Trending 的实时榜单未能成功获取" in result.briefing.spoken_text
    assert "GitHub 热榜无法访问" not in result.briefing.spoken_text


def _github_repositories(count: int, *, prefix: str) -> list[GitHubTrendingRepo]:
    return [
        GitHubTrendingRepo(
            rank=rank,
            full_name=f"{prefix}/repo-{rank}",
            url=f"https://github.com/{prefix}/repo-{rank}",
            description=f"Repository {rank}",
        )
        for rank in range(1, count + 1)
    ]


def _service(
    path: Path,
    repository: SQLiteRepository,
    now: datetime,
    github_provider: StaticGitHubProvider,
) -> MorningBriefingService:
    return MorningBriefingService(
        settings=Settings(_env_file=None, morning_database_path=path),
        preferences=PreferencesConfig(),
        repository=repository,
        collector=CollectorService([MixedFreshnessSource(now)], timeout_seconds=1),  # type: ignore[list-item]
        llm_provider=None,
        github_provider=github_provider,  # type: ignore[arg-type]
    )


def _latest_pipeline_metrics(repository: SQLiteRepository) -> dict[str, object]:
    with sqlite3.connect(repository.path) as connection:
        payload = connection.execute(
            "SELECT payload FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
    return json.loads(payload)  # type: ignore[no-any-return]


def _snapshot_row_count(repository: SQLiteRepository, snapshot_date: date) -> int:
    with sqlite3.connect(repository.path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM github_trending_snapshots WHERE snapshot_date = ?",
            (snapshot_date.isoformat(),),
        ).fetchone()[0]


def test_daily_refresh_schema_rejects_removed_weather_location() -> None:
    assert set(DailyRefreshRequest.model_json_schema()["properties"]) == {"local_date"}
    try:
        DailyRefreshRequest.model_validate(
            {"local_date": "2026-08-20", "weather_location": "滨州"}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("removed weather_location must not be silently accepted")
