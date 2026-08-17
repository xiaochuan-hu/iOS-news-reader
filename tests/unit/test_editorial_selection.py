from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import NewsEvent, SourceType
from morning_intelligence.processing import EditorialSelector


def _event(make_item, index: int, category: str, global_score: float) -> NewsEvent:
    item = make_item(
        f"{category} story {index}",
        f"https://example.com/{category}/{index}",
        category=category,
    )
    return NewsEvent(
        id=f"{category}-{index}",
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
        categories=[category],
        global_importance_score=global_score,
        personal_interest_score=1.0 if category == "ai_llm_agent" else 0.67,
        final_score=global_score,
    )


def test_ai_candidates_do_not_monopolize_selection(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {
            "briefing": {"min_events": 5, "max_events": 6},
            "editorial": {"minimum_score": 0.4},
        }
    )
    candidates = [
        *[_event(make_item, index, "ai_llm_agent", 0.78 - index * 0.01) for index in range(8)],
        _event(make_item, 1, "world", 0.70),
        _event(make_item, 2, "world", 0.68),
    ]

    selection = EditorialSelector(preferences).select(candidates)

    assert len(selection.events) == 6
    assert selection.selected_categories["world"] == 2
    assert selection.selected_categories["ai_llm_agent"] == 4


def test_soft_target_does_not_insert_trivial_uk_story(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {
            "briefing": {"min_events": 5, "max_events": 8},
            "editorial": {"minimum_score": 0.5},
        }
    )
    important = _event(make_item, 1, "world", 0.8)
    trivial_uk = _event(make_item, 2, "uk", 0.2)

    selection = EditorialSelector(preferences).select([important, trivial_uk])

    assert [event.id for event in selection.events] == [important.id]
    assert "uk" not in selection.selected_categories


def test_global_importance_outweighs_niche_personal_relevance(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {"briefing": {"min_events": 1, "max_events": 2}, "editorial": {"minimum_score": 0.3}}
    )
    global_story = _event(make_item, 1, "world", 0.9)
    niche_ai = _event(make_item, 2, "ai_llm_agent", 0.45)

    selection = EditorialSelector(preferences).select([niche_ai, global_story])

    assert selection.events[0].id == global_story.id
    assert global_story.editorial_score > niche_ai.editorial_score


def test_min_events_is_not_a_filler_quota(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {
            "briefing": {"min_events": 5, "max_events": 8},
            "editorial": {"minimum_score": 0.5},
        }
    )
    candidates = [
        _event(make_item, 1, "world", 0.8),
        _event(make_item, 2, "technology", 0.75),
        _event(make_item, 3, "uk", 0.1),
    ]

    selection = EditorialSelector(preferences).select(candidates)

    assert len(selection.events) == 2


def test_non_event_hacker_news_tutorial_is_demoted(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {
            "briefing": {"min_events": 1, "max_events": 8},
            "editorial": {"minimum_score": 0.44},
        }
    )
    item = make_item(
        "Linear algebra done right",
        "https://example.com/book",
        source="Hacker News",
        category="technology",
        description="",
    )
    item.source_type = SourceType.HACKERNEWS
    event = NewsEvent(
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
        categories=["technology"],
        global_importance_score=0.61,
        personal_interest_score=0.87,
    )

    selection = EditorialSelector(preferences).select([event])

    assert selection.events == []
