from __future__ import annotations

from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import EditorialSelection, NewsEvent, SourceType

CATEGORY_ORDER = {
    "ai_llm_agent": 1,
    "technology": 2,
    "world": 3,
    "uk": 4,
    "china": 5,
    "finance": 6,
}
CATEGORY_FAMILIES = {
    "ai_llm_agent": "technology",
    "technology": "technology",
}

NON_EVENT_TITLE_SIGNALS = (
    "ask hn",
    "show hn",
    "done right",
    "guide to",
    "how to",
    "introduction to",
    "notes on",
    "tutorial",
)
SUBJECTIVE_TITLE_SIGNALS = (
    "is excellent",
    "is terrible",
    "perversion of",
    "why i ",
    "my thoughts",
)


class EditorialSelector:
    """Select a meaningful, diverse briefing without treating category targets as quotas."""

    def __init__(self, preferences: PreferencesConfig) -> None:
        self.preferences = preferences

    def select(self, events: list[NewsEvent]) -> EditorialSelection:
        eligible: list[NewsEvent] = []
        for event in events:
            event.editorial_score = self._editorial_score(event)
            if event.editorial_score >= self.preferences.editorial.minimum_score:
                eligible.append(event)

        selected: list[NewsEvent] = []
        category_counts: dict[str, int] = {}
        family_counts: dict[str, int] = {}
        remaining = list(eligible)
        while remaining and len(selected) < self.preferences.briefing.max_events:
            best = max(
                remaining,
                key=lambda event: (
                    self._selection_utility(event, family_counts),
                    event.global_importance_score,
                    event.latest_seen_at,
                ),
            )
            if (
                self._selection_utility(best, family_counts)
                < self.preferences.editorial.minimum_score
            ):
                break
            remaining.remove(best)
            selected.append(best)
            category = _primary_category(best)
            category_counts[category] = category_counts.get(category, 0) + 1
            family = _category_family(category)
            family_counts[family] = family_counts.get(family, 0) + 1

        selected = self._editorial_order(selected)
        return EditorialSelection(
            candidate_count=len(events),
            events=selected,
            selected_categories=category_counts,
        )

    def _editorial_score(self, event: NewsEvent) -> float:
        config = self.preferences.editorial
        score = (
            config.global_weight * event.global_importance_score
            + config.personal_weight * event.personal_interest_score
        )
        avoid_weights = [
            self.preferences.avoid[category].weight
            for category in event.categories
            if category in self.preferences.avoid
        ]
        if avoid_weights:
            score *= min(1.0, max(avoid_weights))
        score *= _content_value_multiplier(event)
        return round(min(1.0, max(0.0, score)), 6)

    def _selection_utility(
        self, event: NewsEvent, family_counts: dict[str, int]
    ) -> float:
        config = self.preferences.editorial
        category = _primary_category(event)
        family = _category_family(category)
        count = family_counts.get(family, 0)
        target = _family_target(family, config.category_targets)
        diversity = config.diversity_bonus if count == 0 else 0.0
        overflow = config.category_overflow_penalty * max(0, count + 1 - target)
        evidence_bonus = 0.03 * event.source_diversity
        return event.editorial_score + diversity + evidence_bonus - overflow

    @staticmethod
    def _editorial_order(events: list[NewsEvent]) -> list[NewsEvent]:
        if not events:
            return []
        lead = max(
            events,
            key=lambda event: (event.global_importance_score, event.editorial_score),
        )
        rest = [event for event in events if event.id != lead.id]
        rest.sort(
            key=lambda event: (
                CATEGORY_ORDER.get(_primary_category(event), 3),
                -event.editorial_score,
                -event.latest_seen_at.timestamp(),
            )
        )
        return [lead, *rest]


def _primary_category(event: NewsEvent) -> str:
    return event.categories[0] if event.categories else "world"


def _category_family(category: str) -> str:
    return CATEGORY_FAMILIES.get(category, category)


def _family_target(family: str, targets: dict[str, int]) -> int:
    members = [
        target
        for category, target in targets.items()
        if _category_family(category) == family
    ]
    return max(members, default=1)


def _content_value_multiplier(event: NewsEvent) -> float:
    """Conservatively demote tutorial/opinion HN posts that are not reported news events."""
    title = event.canonical_title.casefold()
    hacker_news_only = bool(event.items) and all(
        item.source_type == SourceType.HACKERNEWS for item in event.items
    )
    if not hacker_news_only:
        return 1.0
    if title.strip().startswith(("http://", "https://")):
        return 0.0
    if any(signal in title for signal in NON_EVENT_TITLE_SIGNALS):
        return 0.45
    if any(signal in title for signal in SUBJECTIVE_TITLE_SIGNALS):
        return 0.65
    return 1.0
