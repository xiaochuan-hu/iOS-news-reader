from __future__ import annotations

from collections import OrderedDict

from morning_intelligence.domain import NewsItem

from .normalize import normalize_title


def deduplicate_items(items: list[NewsItem]) -> list[NewsItem]:
    """Deduplicate exact URLs/titles and preserve source evidence on the richest item."""
    url_groups: OrderedDict[str, list[NewsItem]] = OrderedDict()
    for item in items:
        key = item.canonical_url or f"missing-url:{item.id}"
        url_groups.setdefault(key, []).append(item)

    after_url = [_merge_group(group) for group in url_groups.values()]
    title_groups: OrderedDict[str, list[NewsItem]] = OrderedDict()
    for item in after_url:
        title_groups.setdefault(normalize_title(item.title), []).append(item)
    return [_merge_group(group) for group in title_groups.values()]


def _merge_group(group: list[NewsItem]) -> NewsItem:
    primary = max(group, key=lambda candidate: len(candidate.description or "")).model_copy(
        deep=True
    )
    sources: list[str] = []
    duplicate_count = 0
    for candidate in group:
        sources.extend(candidate.raw_metadata.get("duplicate_sources", [candidate.source]))
        duplicate_count += int(candidate.raw_metadata.get("duplicate_count", 1))
    primary.raw_metadata["duplicate_sources"] = list(dict.fromkeys(sources))
    primary.raw_metadata["duplicate_count"] = duplicate_count
    return primary
