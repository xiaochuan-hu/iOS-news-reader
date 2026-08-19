from __future__ import annotations

from datetime import datetime

from rapidfuzz.fuzz import token_set_ratio

from morning_intelligence.domain import NewsEvent, NewsItem

from .normalize import normalize_title


def title_similarity(left: str, right: str) -> float:
    return token_set_ratio(normalize_title(left), normalize_title(right)) / 100.0


def cluster_items(items: list[NewsItem], threshold: int = 76) -> list[NewsEvent]:
    """Greedily group different publications about the same event."""
    clusters: list[list[NewsItem]] = []
    for item in sorted(items, key=_published_at, reverse=True):
        best_index: int | None = None
        best_score = 0.0
        for index, cluster in enumerate(clusters):
            score = max(title_similarity(item.title, member.title) for member in cluster)
            if score > best_score:
                best_index, best_score = index, score
        if best_index is not None and best_score * 100 >= threshold:
            clusters[best_index].append(item)
        else:
            clusters.append([item])

    return [_to_event(cluster) for cluster in clusters]


def _to_event(items: list[NewsItem]) -> NewsEvent:
    representative = max(items, key=lambda item: (len(item.description), _published_at(item)))
    sources: list[str] = []
    for item in items:
        candidates = item.raw_metadata.get("duplicate_sources", [item.source])
        sources.extend(str(source) for source in candidates)
    unique_sources = list(dict.fromkeys(sources))
    categories = list(dict.fromkeys(item.category for item in items if item.category))
    return NewsEvent(
        canonical_title=representative.title,
        items=items,
        first_seen_at=min(_published_at(item) for item in items),
        latest_seen_at=max(_published_at(item) for item in items),
        categories=categories or ["world"],
        source_count=len(unique_sources),
        source_diversity=min(1.0, len(unique_sources) / 3.0),
    )


def _published_at(item: NewsItem) -> datetime:
    if item.published_at is None:
        raise ValueError("news item missing published_at after freshness filtering")
    return item.published_at
