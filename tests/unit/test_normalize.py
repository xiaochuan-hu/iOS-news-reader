from morning_intelligence.processing import canonicalize_url, normalize_item, normalize_title


def test_canonical_url_removes_tracking_and_fragment() -> None:
    url = "https://EXAMPLE.com:443/story/?id=42&utm_source=x&fbclid=y#part"
    assert canonicalize_url(url) == "https://example.com/story?id=42"


def test_canonical_url_preserves_meaningful_query() -> None:
    assert canonicalize_url("https://example.com/a?page=2&sort=new") == (
        "https://example.com/a?page=2&sort=new"
    )


def test_normalized_title_handles_unicode_punctuation_and_space() -> None:
    assert normalize_title("  ＡI：  New—Model! ") == "ai new model"


def test_normalize_item_decodes_html_entities(make_item) -> None:
    item = make_item(
        "https:&#x2F;&#x2F;example.com&#x2F;story",
        "https://news.example/story",
        description="Research &amp; development update",
    )
    normalized = normalize_item(item)
    assert normalized.title == "https://example.com/story"
    assert normalized.description == "Research & development update"
