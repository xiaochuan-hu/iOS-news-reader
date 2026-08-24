from __future__ import annotations

from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import EditorialSelection, NewsEvent, SourceType

CATEGORY_ORDER = {
    "ai_llm_agent": 1,
    "technology": 2,
    "embodied_ai": 3,
    "robotics": 4,
    "world": 5,
    "uk": 6,
    "china": 7,
    "finance": 8,
}
CATEGORY_FAMILIES = {
    "ai_llm_agent": "technology",
    "technology": "technology",
    "embodied_ai": "robotics",
    "robotics": "robotics",
}
AI_CATEGORIES = frozenset(
    (
        "ai_llm_agent",
        "technology",
        "embodied_ai",
        "robotics",
    )
)
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
LONG_TERM_AI_SIGNALS = (
    "acquisition",
    "acquires",
    "antitrust",
    "investment",
    "partnership",
    "regulation",
    "restructur",
    "safety standard",
    "strategic",
    "收购",
    "战略",
    "监管",
    "投资",
)
FOUNDATIONAL_AI_SIGNALS = (
    "algorithm",
    "architecture",
    "benchmark record",
    "chip",
    "compute cluster",
    "foundation model",
    "inference",
    "new model",
    "training",
    "新模型",
    "算法",
    "基础设施",
    "推理",
    "训练",
)
AGENT_ROBOTICS_SIGNALS = (
    "agent",
    "autonomous",
    "embodied",
    "humanoid",
    "robot",
    "具身",
    "智能体",
    "机器人",
)
RESEARCH_SIGNALS = (
    "arxiv.org",
    "paper",
    "research",
    "study",
    "论文",
    "研究",
)
LOW_VALUE_SOFTWARE_SIGNALS = (
    "bug fix",
    "deprecated",
    "deprecation",
    "menu bar",
    "minor update",
    "plugin update",
    "small update",
    "theme update",
    "widget",
    "小工具",
    "弃用",
    "插件更新",
    "版本更新",
)


class EditorialSelector:
    """Select a meaningful, diverse briefing without treating category targets as quotas."""

    def __init__(self, preferences: PreferencesConfig) -> None:
        self.preferences = preferences

    def select(self, events: list[NewsEvent]) -> EditorialSelection:
        eligible: list[NewsEvent] = []
        for event in events:
            event.editorial_score = self._editorial_score(event)
            if (
                event.editorial_score >= self.preferences.editorial.minimum_score
                and not self._is_low_impact_research(event)
            ):
                eligible.append(event)

        ai_candidates = [event for event in eligible if _is_ai_event(event)]
        general_candidates = [event for event in eligible if not _is_ai_event(event)]

        # V5.1 first separates structural AI developments from ordinary software
        # churn, then applies the V5 impact/novelty/technical/authority ordering.
        ai_candidates.sort(key=_ai_priority, reverse=True)
        selected: list[NewsEvent] = ai_candidates[: self.preferences.editorial.ai_top_items]
        category_counts: dict[str, int] = {}
        family_counts: dict[str, int] = {}
        for event in selected:
            category = _primary_category(event)
            category_counts[category] = category_counts.get(category, 0) + 1
            family = _category_family(category)
            family_counts[family] = family_counts.get(family, 0) + 1

        general_selected = 0
        remaining = list(general_candidates)
        while (
            remaining
            and general_selected < self.preferences.editorial.general_top_items
            and len(selected) < self.preferences.briefing.max_events
        ):
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
            general_selected += 1
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

    def _is_low_impact_research(self, event: NewsEvent) -> bool:
        return (
            _is_ai_event(event)
            and _is_research_paper(event)
            and event.global_importance_score
            < self.preferences.editorial.ai_research_minimum_impact
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
        # Diversity is a nudge, not an escalating quota wall. Important world
        # stories can still fill the expanded V5.1 general-news allowance.
        overflow = config.category_overflow_penalty * min(1, max(0, count + 1 - target))
        evidence_bonus = 0.03 * event.source_diversity
        analysis_bonus = 0.04 * _analysis_depth_score(event)
        return (
            event.editorial_score
            + diversity
            + evidence_bonus
            + analysis_bonus
            - overflow
        )

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


def _is_ai_event(event: NewsEvent) -> bool:
    return bool(AI_CATEGORIES.intersection(event.categories))


def _ai_priority(
    event: NewsEvent,
) -> tuple[int, float, float, float, float, float, float]:
    return (
        _ai_strategic_tier(event),
        event.global_importance_score,
        _analysis_depth_score(event),
        event.novelty_score,
        event.llm_importance_score,
        event.source_score,
        event.latest_seen_at.timestamp(),
    )


def _is_research_paper(event: NewsEvent) -> bool:
    text = " ".join(
        [
            event.canonical_title,
            *(item.url for item in event.items),
            *(item.source for item in event.items),
        ]
    ).casefold()
    return any(
        signal in text
        for signal in ("arxiv.org", "research paper", "new paper", "论文", "paper:")
    )


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
    multiplier = 1.0
    if _is_ai_event(event) and _is_low_value_software_update(event):
        multiplier *= 0.45
    hacker_news_only = bool(event.items) and all(
        item.source_type == SourceType.HACKERNEWS for item in event.items
    )
    if not hacker_news_only:
        return multiplier
    if title.strip().startswith(("http://", "https://")):
        return 0.0
    if any(signal in title for signal in NON_EVENT_TITLE_SIGNALS):
        return multiplier * 0.45
    if any(signal in title for signal in SUBJECTIVE_TITLE_SIGNALS):
        return multiplier * 0.65
    return multiplier


def _event_text(event: NewsEvent) -> str:
    return " ".join(
        (
            event.canonical_title,
            *(item.title for item in event.items),
            *(item.description for item in event.items),
            *(item.url for item in event.items),
        )
    ).casefold()


def _ai_strategic_tier(event: NewsEvent) -> int:
    text = _event_text(event)
    if any(signal in text for signal in LONG_TERM_AI_SIGNALS):
        return 5
    if any(signal in text for signal in FOUNDATIONAL_AI_SIGNALS):
        return 4
    if any(signal in text for signal in AGENT_ROBOTICS_SIGNALS):
        return 3
    if any(signal in text for signal in RESEARCH_SIGNALS):
        return 2
    return 1


def _is_low_value_software_update(event: NewsEvent) -> bool:
    text = _event_text(event)
    has_structural_signal = any(
        signal in text
        for signal in (
            *LONG_TERM_AI_SIGNALS,
            *FOUNDATIONAL_AI_SIGNALS,
            *AGENT_ROBOTICS_SIGNALS,
        )
    )
    return not has_structural_signal and any(
        signal in text for signal in LOW_VALUE_SOFTWARE_SIGNALS
    )


def _analysis_depth_score(event: NewsEvent) -> float:
    descriptions = {
        " ".join(item.description.split()).casefold()
        for item in event.items
        if item.description.strip()
    }
    evidence_characters = sum(len(description) for description in descriptions)
    return min(1.0, evidence_characters / 180 + min(event.source_count, 3) / 6)
