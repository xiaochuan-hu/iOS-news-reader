from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from morning_intelligence.domain import Briefing, BriefingSection


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class GenerateBriefingRequest(BaseModel):
    mode: Literal["wake", "manual"] = "wake"
    target_minutes: int | None = Field(default=None, ge=1, le=10)
    incremental_refresh: bool = True
    max_age_minutes: int = Field(default=30, ge=1, le=1440)


class BriefingResponse(BaseModel):
    id: str
    generated_at: datetime
    freshness_minutes: int
    event_count: int
    estimated_duration_seconds: int
    spoken_text: str
    sections: list[BriefingSection]
    sources: list[str]
    is_fallback: bool = False

    @classmethod
    def from_domain(cls, briefing: Briefing) -> BriefingResponse:
        freshness = max(
            0,
            round((datetime.now(UTC) - briefing.generated_at).total_seconds() / 60),
        )
        return cls(
            id=briefing.id,
            generated_at=briefing.generated_at,
            freshness_minutes=freshness,
            event_count=briefing.event_count,
            estimated_duration_seconds=briefing.estimated_duration_seconds,
            spoken_text=briefing.spoken_text,
            sections=briefing.sections,
            sources=briefing.sources,
            is_fallback=briefing.is_fallback,
        )
