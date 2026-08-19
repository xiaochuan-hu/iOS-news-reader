import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from morning_intelligence.domain import Briefing, GitHubTrendingRepo, NewsEvent
from morning_intelligence.processing import normalize_item
from morning_intelligence.storage import SQLiteRepository


def test_latest_briefing_uses_cache_without_refresh(tmp_path) -> None:
    from morning_intelligence.config import PreferencesConfig, Settings
    from morning_intelligence.domain import Briefing
    from morning_intelligence.services import CollectorService, MorningBriefingService

    repository = SQLiteRepository(tmp_path / "cached.db")
    repository.initialize()
    cached = Briefing(
        event_count=1,
        estimated_duration_seconds=12,
        spoken_text="缓存晨报",
    )
    repository.save_briefing(cached)
    service = MorningBriefingService(
        settings=Settings(_env_file=None, morning_database_path=tmp_path / "cached.db"),
        preferences=PreferencesConfig(),
        repository=repository,
        collector=CollectorService([], timeout_seconds=1),
        llm_provider=None,
    )

    result = service.latest()

    assert result is not None
    assert result.id == cached.id


def test_sqlite_round_trip_and_novelty_history(tmp_path, make_item) -> None:
    repository = SQLiteRepository(tmp_path / "test.db")
    repository.initialize()
    item = normalize_item(make_item("Important event", "https://example.com/event"))
    event = NewsEvent(
        id="event-1",
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
        final_score=0.8,
    )
    repository.save_items([item])
    repository.save_events([event])
    briefing = Briefing(
        generated_at=datetime.now(UTC),
        event_count=1,
        estimated_duration_seconds=10,
        spoken_text="早上好。",
        event_ids=[event.id],
    )
    repository.save_briefing(briefing)

    events, saved_at = repository.get_latest_events()
    assert saved_at is not None
    assert events[0].canonical_title == "Important event"
    assert repository.get_latest_briefing() == briefing
    assert repository.recent_spoken_titles(3) == ["Important event"]


def test_budget_same_date_and_rolling_interval_are_fail_closed(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "budget.db")
    repository.initialize()
    start = datetime(2026, 8, 19, 21, tzinfo=UTC)

    first = _claim(repository, date(2026, 8, 20), start)
    same_day = _claim(repository, date(2026, 8, 20), start + timedelta(hours=2))
    travel_day = _claim(repository, date(2026, 8, 21), start + timedelta(hours=19, minutes=59))
    next_day = _claim(repository, date(2026, 8, 21), start + timedelta(hours=20))

    assert first.claimed is True
    assert same_day.claimed is False and same_day.reason == "already_claimed"
    assert travel_day.claimed is False and travel_day.reason == "min_interval"
    assert next_day.claimed is True


def test_failed_completed_claim_remains_consumed_after_restart(tmp_path) -> None:
    path = tmp_path / "restart.db"
    first_process = SQLiteRepository(path)
    first_process.initialize()
    now = datetime(2026, 8, 19, 5, tzinfo=UTC)
    claimed = _claim(first_process, date(2026, 8, 19), now)
    assert claimed.claim is not None
    first_process.complete_llm_budget_claim(
        claimed.claim.id,
        success=False,
        error="httpx.ReadTimeout",
        completed_at_utc=now + timedelta(minutes=1),
    )

    second_process = SQLiteRepository(path)
    second_process.initialize()
    repeated = _claim(second_process, date(2026, 8, 19), now + timedelta(hours=3))

    assert repeated.claimed is False
    assert repeated.reason == "already_claimed"
    assert repeated.claim is not None and repeated.claim.success is False


def test_concurrent_budget_claims_have_exactly_one_winner(tmp_path) -> None:
    path = tmp_path / "concurrent.db"
    SQLiteRepository(path).initialize()
    now = datetime(2026, 8, 19, 5, tzinfo=UTC)

    def attempt(_: int):
        return _claim(SQLiteRepository(path), date(2026, 8, 19), now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))

    assert sum(result.claimed for result in results) == 1
    assert sorted(result.reason for result in results) == ["already_claimed", "claimed"]


def test_additive_migration_preserves_legacy_briefing(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    legacy = Briefing(
        generated_at=datetime(2026, 8, 18, 5, tzinfo=UTC),
        event_count=1,
        estimated_duration_seconds=12,
        spoken_text="旧版缓存晨报",
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE briefings (
                id TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
                spoken_text TEXT NOT NULL, payload TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO briefings VALUES (?, ?, ?, ?)",
            (
                legacy.id,
                legacy.generated_at.isoformat(),
                legacy.spoken_text,
                legacy.model_dump_json(),
            ),
        )

    repository = SQLiteRepository(path)
    repository.initialize()

    assert repository.get_latest_briefing() == legacy
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'llm_daily_budget'"
        ).fetchone() is not None


def test_github_snapshot_round_trip_is_scoped_to_exact_date(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "github.db")
    repository.initialize()
    snapshot_date = date(2026, 8, 20)
    repositories = [
        GitHubTrendingRepo(
            rank=rank,
            full_name=f"owner/repo-{rank}",
            url=f"https://github.com/owner/repo-{rank}",
            description=f"Repo {rank}",
            stars_today=rank * 10,
        )
        for rank in range(1, 11)
    ]

    repository.save_github_trending_snapshot(
        snapshot_date,
        repositories,
        fetched_at_utc=datetime(2026, 8, 20, 5, tzinfo=UTC),
    )

    assert repository.get_github_trending_snapshot(snapshot_date) == repositories
    assert repository.get_github_trending_snapshot(date(2026, 8, 19)) == []
    with sqlite3.connect(repository.path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert all("weather" not in name.casefold() for name in table_names)


def _claim(repository: SQLiteRepository, local_date: date, now: datetime):
    return repository.claim_llm_budget(
        purpose="morning_editorial",
        local_date=local_date,
        now_utc=now,
        minimum_interval=timedelta(hours=20),
        provider="openai_compatible",
        model="test-model",
    )
