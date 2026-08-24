from __future__ import annotations

from datetime import datetime

from rapidfuzz.fuzz import partial_ratio, token_set_ratio

from morning_intelligence.domain import NewsEvent, NewsItem

from .normalize import normalize_title

GENERIC_EVENT_TOKENS = frozenset(
    {
        "about",
        "after",
        "announces",
        "company",
        "event",
        "from",
        "latest",
        "launches",
        "news",
        "report",
        "reports",
        "says",
        "the",
        "this",
        "update",
        "with",
    }
)
AI_EVENT_CATEGORIES = frozenset(
    {"ai_llm_agent", "technology", "embodied_ai", "robotics"}
)


def title_similarity(left: str, right: str) -> float:
    return token_set_ratio(normalize_title(left), normalize_title(right)) / 100.0


def cluster_items(items: list[NewsItem], threshold: int = 76) -> list[NewsEvent]:
    """Greedily group different publications about the same event."""
    clusters: list[list[NewsItem]] = []
    for item in sorted(items, key=_published_at, reverse=True):
        best_index: int | None = None
        best_score = 0.0
        for index, cluster in enumerate(clusters):
            score = max(_cluster_similarity(item, member) for member in cluster)
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


def _cluster_similarity(left: NewsItem, right: NewsItem) -> float:
    """Combine conservative title similarity with shared event-signature evidence."""
    title_score = title_similarity(left.title, right.title)
    if not _categories_are_compatible(left.category, right.category):
        return title_score

    left_title = normalize_title(left.title)
    right_title = normalize_title(right.title)
    left_tokens = _significant_tokens(left_title)
    right_tokens = _significant_tokens(right_title)
    shared = left_tokens.intersection(right_tokens)
    smaller_token_count = min(len(left_tokens), len(right_tokens))
    containment = len(shared) / smaller_token_count if smaller_token_count else 0.0

    if len(shared) >= 3 and containment >= 0.5:
        title_score = max(title_score, 0.84)
    elif len(shared) >= 2 and containment >= 0.4 and title_score >= 0.55:
        title_score = max(title_score, 0.79)

    shorter_length = min(len(left_title), len(right_title))
    partial_score = partial_ratio(left_title, right_title) / 100.0
    if shorter_length >= 16 and partial_score >= 0.88:
        title_score = max(title_score, 0.80)

    return title_score


def _significant_tokens(title: str) -> set[str]:
    return {
        token
        for token in title.split()
        if len(token) >= 3 and token not in GENERIC_EVENT_TOKENS
    }


def _categories_are_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    return left in AI_EVENT_CATEGORIES and right in AI_EVENT_CATEGORIES
