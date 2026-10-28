import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from morning_intelligence.domain import Briefing, EmailMessage, GitHubTrendingRepo, NewsEvent
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


def test_recent_briefings_are_reverse_chronological_and_limited(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "recent.db")
    repository.initialize()
    briefings = [
        Briefing(
            id=f"briefing-{day}",
            generated_at=datetime(2026, 10, day, 5, tzinfo=UTC),
            event_count=1,
            estimated_duration_seconds=10,
            spoken_text=f"Briefing {day}",
        )
        for day in (26, 28, 27)
    ]
    for briefing in briefings:
        repository.save_briefing(briefing)

    assert [
        briefing.id for briefing in repository.get_recent_briefings(2)
    ] == ["briefing-28", "briefing-27"]
    assert repository.get_recent_briefings(0) == []


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


def test_email_round_trip_reopen_exact_window_and_feedback_filter(tmp_path) -> None:
    path = tmp_path / "email.db"
    repository = SQLiteRepository(path)
    repository.initialize()
    start = datetime(2026, 10, 27, 6, tzinfo=UTC)
    end = datetime(2026, 10, 28, 6, tzinfo=UTC)
    messages = [
        _email("before", start - timedelta(microseconds=1)),
        _email("start", start),
        _email("middle", start + timedelta(hours=12)),
        _email("end", end),
        _email("after", end + timedelta(microseconds=1)),
        _email("feedback", start + timedelta(hours=1), subject="UCL Morning Brief | 2026-10-27"),
    ]

    assert repository.upsert_email_messages(messages) == 5
    reopened = SQLiteRepository(path)
    reopened.initialize()
    result = reopened.get_email_messages(start, end)

    assert [message.message_id for message in result] == ["start", "middle", "end"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM email_messages WHERE message_id = 'feedback'"
        ).fetchone()[0] == 0


def test_email_upsert_is_idempotent_by_gmail_message_id(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "email-idempotent.db")
    repository.initialize()
    received_at = datetime(2026, 10, 28, 5, tzinfo=UTC)

    repository.upsert_email_messages([_email("gmail-stable-id", received_at, subject="First")])
    repository.upsert_email_messages([_email("gmail-stable-id", received_at, subject="Updated")])

    result = repository.get_email_messages(received_at, received_at)
    assert len(result) == 1
    assert result[0].subject == "Updated"


def _email(message_id: str, received_at: datetime, *, subject: str = "Module update"):
    return EmailMessage(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        received_at=received_at,
        sender="UCL <ucl@example.test>",
        recipients=["student@example.test"],
        cc=[],
        subject=subject,
        snippet="A useful update",
        body_text="Evidence-backed email body",
    )


def _claim(repository: SQLiteRepository, local_date: date, now: datetime):
    return repository.claim_llm_budget(
        purpose="morning_editorial",
        local_date=local_date,
        now_utc=now,
        minimum_interval=timedelta(hours=20),
        provider="openai_compatible",
        model="test-model",
    )
