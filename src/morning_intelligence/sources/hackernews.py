from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from morning_intelligence.config.settings import HackerNewsConfig
from morning_intelligence.domain import NewsItem, SourceType


class HackerNewsSource:
    name = "Hacker News"
    base_url = "https://hacker-news.firebaseio.com/v0"

    def __init__(self, config: HackerNewsConfig) -> None:
        self.config = config

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]:
        response = await client.get(f"{self.base_url}/topstories.json")
        response.raise_for_status()
        story_ids = response.json()[: self.config.fetch_top_stories]
        stories = await asyncio.gather(
            *(self._get_item(int(story_id), client) for story_id in story_ids),
            return_exceptions=True,
        )
        items: list[NewsItem] = []
        for story in stories:
            if not isinstance(story, dict) or story.get("score", 0) < self.config.min_score:
                continue
            published = datetime.fromtimestamp(story["time"], tz=UTC)
            if published < since:
                continue
            story_id = int(story["id"])
            discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
            url = str(story.get("url") or discussion_url)
            description = re.sub(r"<[^>]+>", " ", str(story.get("text") or ""))
            items.append(
                NewsItem(
                    id=f"hackernews:story:{story_id}",
                    source=self.name,
                    source_type=SourceType.HACKERNEWS,
                    title=str(story.get("title") or "Untitled"),
                    url=url,
                    description=description,
                    published_at=published,
                    author=story.get("by"),
                    category=self.config.category,
                    source_quality=self.config.quality,
                    raw_metadata={
                        "score": story.get("score", 0),
                        "comments": story.get("descendants", 0),
                        "discussion_url": discussion_url,
                    },
                )
            )
        return items

    async def _get_item(self, item_id: int, client: httpx.AsyncClient) -> dict[str, Any] | None:
        response = await client.get(f"{self.base_url}/item/{item_id}.json")
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else None
