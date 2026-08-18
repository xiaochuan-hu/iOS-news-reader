from __future__ import annotations

from abc import ABC, abstractmethod

from morning_intelligence.domain import EditorialBriefingResult, NewsEvent


class LLMProvider(ABC):
    @abstractmethod
    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult: ...
