from __future__ import annotations

import calendar
import hashlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from morning_intelligence.config.settings import RSSFeedConfig
from morning_intelligence.domain import NewsItem, SourceType


class RSSSource:
    def __init__(self, config: RSSFeedConfig) -> None:
        self.config = config
        self.name = config.name

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]:
        response = await client.get(str(self.config.url), follow_redirects=True)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        items: list[NewsItem] = []
        for entry in feed.entries:
            published_at = _parse_date(entry)
            if published_at is None or published_at < since:
                continue
            url = str(entry.get("link") or self.config.url)
            native_id = str(entry.get("id") or url)
            digest = hashlib.sha256(native_id.encode()).hexdigest()[:20]
            items.append(
                NewsItem(
                    id=f"rss:{hashlib.sha256(self.name.encode()).hexdigest()[:8]}:{digest}",
                    source=self.name,
                    source_type=SourceType.RSS,
                    title=str(entry.get("title") or "Untitled"),
                    url=url,
                    description=_description(entry),
                    published_at=published_at,
                    author=entry.get("author"),
                    category=self.config.category,
                    source_quality=self.config.quality,
                    raw_metadata={"tags": [tag.get("term", "") for tag in entry.get("tags", [])]},
                )
            )
        return items


def _parse_date(entry: Any) -> datetime | None:
    for field in ("published", "updated", "created"):
        structured = entry.get(f"{field}_parsed")
        if structured:
            return datetime.fromtimestamp(calendar.timegm(structured), tz=UTC)
        raw = entry.get(field)
        if raw:
            try:
                parsed = parsedate_to_datetime(str(raw))
                return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _description(entry: Any) -> str:
    if entry.get("summary"):
        return str(entry.summary)
    if entry.get("description"):
        return str(entry.description)
    content = entry.get("content") or []
    return str(content[0].get("value", "")) if content else ""
