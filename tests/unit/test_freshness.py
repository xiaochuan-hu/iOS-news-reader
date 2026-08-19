from datetime import UTC, datetime, timedelta

from morning_intelligence.domain import NewsItem, SourceType
from morning_intelligence.services import filter_fresh_news_items


def _item(published_at: datetime | None, suffix: str) -> NewsItem:
    return NewsItem(
        source="fixture",
        source_type=SourceType.RSS,
        title=f"event {suffix}",
        url=f"https://example.com/{suffix}",
        published_at=published_at,
    )


def test_authoritative_rolling_24_hour_boundaries() -> None:
    end = datetime(2026, 8, 20, 5, tzinfo=UTC)
    start = end - timedelta(hours=24)
    items = [
        _item(end - timedelta(hours=23, minutes=59), "2359"),
        _item(start, "exact-24h"),
        _item(end - timedelta(hours=24, minutes=1), "2401"),
        _item(end - timedelta(hours=48), "48h"),
        _item(None, "missing"),
        _item(end + timedelta(seconds=1), "future"),
    ]

    result = filter_fresh_news_items(
        items, window_start_utc=start, window_end_utc=end
    )

    assert [item.title for item in result.items] == ["event 2359", "event exact-24h"]
    assert result.stale_rejected == 2
    assert result.missing_timestamp_rejected == 1
    assert result.future_rejected == 1
