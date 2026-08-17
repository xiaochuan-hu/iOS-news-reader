from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from morning_intelligence.domain import NewsItem, SourceType


@pytest.fixture
def make_item():
    def factory(
        title: str,
        url: str,
        *,
        source: str = "Example",
        category: str = "technology",
        age_hours: int = 1,
        description: str = "A concrete description of the reported event.",
        quality: float = 0.8,
    ) -> NewsItem:
        return NewsItem(
            source=source,
            source_type=SourceType.RSS,
            title=title,
            url=url,
            description=description,
            published_at=datetime.now(UTC) - timedelta(hours=age_hours),
            category=category,
            source_quality=quality,
        )

    return factory
