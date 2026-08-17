from __future__ import annotations

from datetime import UTC, datetime

from morning_intelligence.domain import Briefing, BriefingSection, NewsEvent
from morning_intelligence.llm import LLMProvider

from .spoken_script import (
    deterministic_spoken_script,
    estimate_duration_seconds,
    sanitize_spoken_text,
)


class BriefingGenerator:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    async def generate(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
    ) -> tuple[Briefing, bool]:
        llm_used = False
        now = datetime.now(UTC)
        spoken_text = ""
        if self.provider and events:
            try:
                spoken_text = await self.provider.create_spoken_script(
                    events, target_minutes=target_minutes, language=language
                )
                spoken_text = sanitize_spoken_text(spoken_text)
                llm_used = bool(spoken_text)
            except Exception:
                spoken_text = ""
        if not spoken_text:
            spoken_text = deterministic_spoken_script(events, now=now)

        sections = _sections(events)
        sources = list(
            dict.fromkeys(item.source for event in events for item in event.items)
        )
        briefing = Briefing(
            generated_at=now,
            event_count=len(events),
            estimated_duration_seconds=estimate_duration_seconds(spoken_text),
            spoken_text=spoken_text,
            sections=sections,
            sources=sources,
            event_ids=[event.id for event in events],
        )
        return briefing, llm_used


def _sections(events: list[NewsEvent]) -> list[BriefingSection]:
    grouped: dict[str, list[NewsEvent]] = {}
    for event in events:
        category = event.categories[0] if event.categories else "world"
        grouped.setdefault(category, []).append(event)
    return [
        BriefingSection(
            name=category,
            event_ids=[event.id for event in group],
            text="；".join(event.canonical_title for event in group),
        )
        for category, group in grouped.items()
    ]
