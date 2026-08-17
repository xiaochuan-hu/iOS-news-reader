from datetime import UTC, datetime

from morning_intelligence.domain import Briefing, NewsEvent
from morning_intelligence.processing import normalize_item
from morning_intelligence.storage import SQLiteRepository


async def test_latest_briefing_uses_cache_without_refresh(tmp_path) -> None:
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

    async def unexpected_refresh() -> Briefing:
        raise AssertionError("cached latest request must not refresh")

    service.refresh = unexpected_refresh  # type: ignore[method-assign]

    result = await service.latest_or_generate()

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
