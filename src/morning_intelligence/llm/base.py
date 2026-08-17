from __future__ import annotations

from abc import ABC, abstractmethod

from morning_intelligence.domain import NewsEvent


class LLMProvider(ABC):
    @abstractmethod
    async def score_events(self, events: list[NewsEvent]) -> dict[str, float]: ...

    @abstractmethod
    async def summarize_events(self, events: list[NewsEvent]) -> dict[str, tuple[str, str]]: ...

    @abstractmethod
    async def create_spoken_script(
        self, events: list[NewsEvent], *, target_minutes: int, language: str
    ) -> str: ...
