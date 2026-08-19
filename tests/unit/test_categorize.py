from morning_intelligence.processing import infer_category, normalize_item


def test_infers_ai_interest_from_broad_feed(make_item) -> None:
    item = make_item(
        "OpenAI releases a new language model for developers",
        "https://example.com/ai",
        category="world",
    )
    assert normalize_item(item).category == "ai_llm_agent"


def test_infers_avoid_category_before_general_world(make_item) -> None:
    item = make_item(
        "Actress announces celebrity reality show",
        "https://example.com/show",
        category="world",
    )
    assert normalize_item(item).category == "celebrity_gossip"


def test_preserves_configured_category_when_no_keyword_matches() -> None:
    assert infer_category(["A diplomatic meeting took place"], "world") == "world"


def test_infers_low_value_viral_variants() -> None:
    assert infer_category(["Movie that went viral and won ironic fans"], "world") == (
        "low_value_viral"
    )


def test_infers_embodied_ai_before_generic_ai() -> None:
    assert infer_category(
        ["New embodied AI vision-language-action system for robot manipulation"],
        "technology",
    ) == "embodied_ai"


def test_infers_robotics_without_generic_ai_keyword() -> None:
    assert infer_category(
        ["Humanoid robotics platform demonstrates autonomous robot navigation"],
        "technology",
    ) == "robotics"
