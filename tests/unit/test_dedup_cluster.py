from morning_intelligence.processing import cluster_items, deduplicate_items, normalize_item


def test_exact_url_dedup_keeps_richest_and_source_evidence(make_item) -> None:
    short = make_item(
        "OpenAI releases GPT-6",
        "https://example.com/story?utm_source=x",
        source="Wire A",
        description="short",
    )
    rich = make_item(
        "OpenAI releases GPT-6",
        "https://example.com/story",
        source="Wire B",
        description="a much richer description",
    )
    result = deduplicate_items([normalize_item(short), normalize_item(rich)])
    assert len(result) == 1
    assert result[0].description == "a much richer description"
    assert result[0].raw_metadata["duplicate_sources"] == ["Wire A", "Wire B"]


def test_three_publications_become_one_news_event(make_item) -> None:
    items = [
        make_item(
            "OpenAI releases new GPT-6 model for developers",
            "https://reuters.example/gpt6",
            source="Reuters",
        ),
        make_item(
            "OpenAI releases GPT-6 model aimed at developers",
            "https://bbc.example/openai-gpt6",
            source="BBC",
        ),
        make_item(
            "New GPT-6 model released by OpenAI for developers",
            "https://verge.example/gpt-6",
            source="The Verge",
        ),
    ]
    events = cluster_items([normalize_item(item) for item in items], threshold=70)
    assert len(events) == 1
    assert events[0].source_count == 3
    assert len(events[0].items) == 3


def test_exact_normalized_title_is_second_dedup_layer(make_item) -> None:
    items = [
        normalize_item(make_item("Same Event!", "https://a.example/story", source="A")),
        normalize_item(make_item("same event", "https://b.example/report", source="B")),
    ]
    result = deduplicate_items(items)
    assert len(result) == 1
    assert result[0].raw_metadata["duplicate_sources"] == ["A", "B"]


def test_distinct_events_about_same_company_stay_separate(make_item) -> None:
    items = [
        normalize_item(make_item("OpenAI releases GPT-6", "https://a.example/release")),
        normalize_item(make_item("OpenAI opens a new office in London", "https://b.example/office")),
    ]
    assert len(cluster_items(items, threshold=76)) == 2
