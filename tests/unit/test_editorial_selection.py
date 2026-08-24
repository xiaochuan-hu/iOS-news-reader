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


def test_v5_selects_exactly_five_ai_items_and_fewer_general_items(make_item) -> None:
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
    assert selection.selected_categories["world"] == 1
    assert selection.selected_categories["ai_llm_agent"] == 5


def test_v51_allows_five_high_quality_general_stories(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {
            "briefing": {"min_events": 1, "max_events": 10},
            "editorial": {"minimum_score": 0.4, "general_top_items": 5},
        }
    )
    categories = ("world", "uk", "china", "finance", "world", "world")
    candidates = [
        _event(make_item, index, category, 0.9 - index * 0.02)
        for index, category in enumerate(categories)
    ]

    selection = EditorialSelector(preferences).select(candidates)

    assert len(selection.events) == 5


def test_v51_demotes_small_software_churn_below_foundational_ai(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {"briefing": {"min_events": 1, "max_events": 5}}
    )
    widget = _event(make_item, 1, "technology", 0.9)
    widget.canonical_title = "A small macOS widget is deprecated"
    infrastructure = _event(make_item, 2, "ai_llm_agent", 0.7)
    infrastructure.canonical_title = "New AI inference infrastructure cuts serving overhead"

    selection = EditorialSelector(preferences).select([widget, infrastructure])

    assert [event.id for event in selection.events] == [infrastructure.id]


def test_v5_ai_ranking_is_impact_then_novelty_technical_authority(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {"briefing": {"min_events": 1, "max_events": 5}, "editorial": {"minimum_score": 0.1}}
    )
    events = [_event(make_item, index, "ai_llm_agent", 0.8) for index in range(6)]
    for index, event in enumerate(events):
        event.novelty_score = 0.5
        event.llm_importance_score = index / 10
        event.source_score = (6 - index) / 10
    events[-1].global_importance_score = 0.81

    selection = EditorialSelector(preferences).select(events)

    assert len(selection.events) == 5
    assert selection.events[0].id == events[-1].id
    assert selection.events[1].id == events[-2].id


def test_v5_excludes_low_impact_ai_research_paper(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {"briefing": {"min_events": 1, "max_events": 5}, "editorial": {"minimum_score": 0.1}}
    )
    paper = _event(make_item, 1, "ai_llm_agent", 0.6)
    paper.canonical_title = "New paper: incremental benchmark result"
    consequential = _event(make_item, 2, "ai_llm_agent", 0.8)
    consequential.canonical_title = "New paper: high-impact robotics safety result"

    selection = EditorialSelector(preferences).select([paper, consequential])

    assert [event.id for event in selection.events] == [consequential.id]


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


def test_robotics_target_is_soft_and_never_bypasses_minimum_score(make_item) -> None:
    preferences = PreferencesConfig.model_validate(
        {
            "briefing": {"min_events": 1, "max_events": 5},
            "editorial": {"minimum_score": 0.5},
        }
    )
    important_world = _event(make_item, 1, "world", 0.82)
    important_robotics = _event(make_item, 2, "robotics", 0.76)
    stale_quality_robotics = _event(make_item, 3, "embodied_ai", 0.15)

    selection = EditorialSelector(preferences).select(
        [important_world, important_robotics, stale_quality_robotics]
    )

    assert {event.id for event in selection.events} == {
        important_world.id,
        important_robotics.id,
    }
