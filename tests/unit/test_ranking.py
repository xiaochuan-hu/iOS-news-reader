from datetime import UTC, datetime

from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import NewsEvent
from morning_intelligence.processing import rank_events, score_event


def test_hybrid_score_rewards_confirmation_and_penalizes_replay(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {
            "interests": {"technology": {"weight": 1.5}},
            "avoid": {},
        }
    )
    now = datetime.now(UTC)
    item = make_item("Major model release", "https://a.example/story")
    fresh = NewsEvent(
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
        categories=["technology"],
        source_count=3,
        source_diversity=1,
    )
    replay = fresh.model_copy(deep=True, update={"id": "replay"})
    score_event(fresh, preferences, now=now, was_recently_spoken=False)
    score_event(replay, preferences, now=now, was_recently_spoken=True)
    assert fresh.final_score > replay.final_score
    assert fresh.cross_source_score == 1


def test_ranking_applies_max_events(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {"briefing": {"max_events": 2, "min_event_score": 0.1}}
    )
    events = []
    for index, value in enumerate((0.8, 0.6, 0.4)):
        item = make_item(f"Story {index}", f"https://example.com/{index}")
        events.append(
            NewsEvent(
                canonical_title=item.title,
                items=[item],
                first_seen_at=item.published_at,
                latest_seen_at=item.published_at,
                final_score=value,
            )
        )
    assert [event.final_score for event in rank_events(events, preferences)] == [0.8, 0.6]
