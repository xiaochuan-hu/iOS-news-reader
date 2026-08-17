from __future__ import annotations

from datetime import UTC, datetime

from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import NewsEvent


def score_event(
    event: NewsEvent,
    preferences: PreferencesConfig,
    *,
    now: datetime | None = None,
    was_recently_spoken: bool = False,
) -> NewsEvent:
    current = now or datetime.now(UTC)
    age_hours = max(0.0, (current - event.latest_seen_at).total_seconds() / 3600)
    event.recency_score = max(0.0, 1.0 - age_hours / preferences.processing.lookback_hours)
    event.source_score = sum(item.source_quality for item in event.items) / len(event.items)
    event.cross_source_score = min(1.0, max(0, event.source_count - 1) / 2)

    interest_weights: list[float] = []
    for category in event.categories:
        if category in preferences.avoid:
            interest_weights.append(preferences.avoid[category].weight)
        elif category in preferences.interests:
            interest_weights.append(preferences.interests[category].weight)
        else:
            interest_weights.append(1.0)
    event.personal_interest_score = min(1.0, max(interest_weights, default=1.0) / 1.5)
    event.novelty_score = 0.15 if was_recently_spoken else 1.0
    event.global_importance_score = round(
        0.45 * event.recency_score
        + 0.35 * event.source_score
        + 0.20 * event.cross_source_score,
        6,
    )
    event.final_score = calculate_final_score(event, preferences)
    return event


def calculate_final_score(event: NewsEvent, preferences: PreferencesConfig) -> float:
    weights = preferences.ranking
    value = (
        weights.recency * event.recency_score
        + weights.source_quality * event.source_score
        + weights.cross_source_confirmation * event.cross_source_score
        + weights.personal_interest * event.personal_interest_score
        + weights.novelty * event.novelty_score
        + weights.llm_importance * event.llm_importance_score
    )
    return round(min(1.0, max(0.0, value)), 6)
