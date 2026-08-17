from __future__ import annotations

from abc import ABC, abstractmethod

from morning_intelligence.domain import EnrichedNewsEvent, EventIntelligence, NewsEvent


class LLMProvider(ABC):
    @abstractmethod
    async def score_events(self, events: list[NewsEvent]) -> dict[str, float]: ...

    @abstractmethod
    async def synthesize_events(
        self, events: list[NewsEvent]
    ) -> dict[str, EventIntelligence]: ...

    @abstractmethod
    async def create_spoken_script(
        self,
        events: list[EnrichedNewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> str: ...
