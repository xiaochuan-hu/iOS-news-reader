from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from morning_intelligence.domain import NewsItem
from morning_intelligence.sources import NewsSource


@dataclass
class SourceOutcome:
    name: str
    succeeded: bool
    items: list[NewsItem] = field(default_factory=list)
    error: str = ""


@dataclass
class CollectionResult:
    outcomes: list[SourceOutcome]

    @property
    def items(self) -> list[NewsItem]:
        return [item for outcome in self.outcomes for item in outcome.items]

    @property
    def all_failed(self) -> bool:
        return bool(self.outcomes) and all(not outcome.succeeded for outcome in self.outcomes)


class CollectorService:
    def __init__(self, sources: list[NewsSource], *, timeout_seconds: float) -> None:
        self.sources = sources
        self.timeout_seconds = timeout_seconds

    async def collect(self, since: datetime) -> CollectionResult:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            outcomes = await asyncio.gather(
                *(self._fetch(source, since, client) for source in self.sources)
            )
        return CollectionResult(outcomes=list(outcomes))

    async def _fetch(
        self, source: NewsSource, since: datetime, client: httpx.AsyncClient
    ) -> SourceOutcome:
        try:
            items = await asyncio.wait_for(
                source.fetch(since, client), timeout=self.timeout_seconds + 0.5
            )
            return SourceOutcome(name=source.name, succeeded=True, items=items)
        except Exception as exc:
            return SourceOutcome(
                name=source.name,
                succeeded=False,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
