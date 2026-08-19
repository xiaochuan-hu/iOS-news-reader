from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from morning_intelligence.domain import Briefing, BriefingSection


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class GenerateBriefingRequest(BaseModel):
    mode: Literal["wake", "manual"] = "wake"
    target_minutes: int | None = Field(default=None, ge=1, le=10)
    incremental_refresh: bool = True
    max_age_minutes: int = Field(default=30, ge=1, le=1440)


class DailyRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_date: date

    @field_validator("local_date", mode="before")
    @classmethod
    def local_date_must_be_strict_iso(cls, value: object) -> date:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("local_date must use strict YYYY-MM-DD format")
        return date.fromisoformat(value)


class DailyRefreshResponse(BaseModel):
    status: Literal[
        "generated",
        "generated_fallback",
        "already_claimed",
        "no_llm_available",
        "cached",
    ]
    local_date: date
    briefing_id: str | None = None
    generated_at: datetime | None = None
    budget_reason: str = ""


def client_local_date_is_valid(local_date: date, *, now_utc: datetime | None = None) -> bool:
    server_date = (now_utc or datetime.now(UTC)).astimezone(UTC).date()
    return server_date - timedelta(days=1) <= local_date <= server_date + timedelta(days=1)


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
    news_window_start: datetime | None = None
    news_window_end: datetime | None = None
    github_trending_count: int = 0

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
            news_window_start=briefing.news_window_start,
            news_window_end=briefing.news_window_end,
            github_trending_count=briefing.github_trending_count,
        )
