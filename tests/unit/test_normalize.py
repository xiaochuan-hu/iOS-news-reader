from morning_intelligence.processing import canonicalize_url, normalize_title


def test_canonical_url_removes_tracking_and_fragment() -> None:
    url = "https://EXAMPLE.com:443/story/?id=42&utm_source=x&fbclid=y#part"
    assert canonicalize_url(url) == "https://example.com/story?id=42"


def test_canonical_url_preserves_meaningful_query() -> None:
    assert canonicalize_url("https://example.com/a?page=2&sort=new") == (
        "https://example.com/a?page=2&sort=new"
    )


def test_normalized_title_handles_unicode_punctuation_and_space() -> None:
    assert normalize_title("  ＡI：  New—Model! ") == "ai new model"
