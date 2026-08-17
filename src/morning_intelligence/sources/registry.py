from __future__ import annotations

from morning_intelligence.config import SourcesConfig

from .base import NewsSource
from .hackernews import HackerNewsSource
from .rss import RSSSource


def build_sources(config: SourcesConfig) -> list[NewsSource]:
    sources: list[NewsSource] = [RSSSource(feed) for feed in config.rss if feed.enabled]
    if config.hackernews.enabled:
        sources.append(HackerNewsSource(config.hackernews))
    return sources
