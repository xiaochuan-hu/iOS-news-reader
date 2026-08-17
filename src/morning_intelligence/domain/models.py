from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import PipelineStatus, SourceType


def utc_now() -> datetime:
    return datetime.now(UTC)


class NewsItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    source: str
    source_type: SourceType
    title: str
    url: str
    canonical_url: str = ""
    description: str = ""
    published_at: datetime
    fetched_at: datetime = Field(default_factory=utc_now)
    author: str | None = None
    language: str | None = None
    category: str = "world"
    source_quality: float = Field(default=0.6, ge=0, le=1)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class NewsEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    canonical_title: str
    items: list[NewsItem]
    first_seen_at: datetime
    latest_seen_at: datetime
    categories: list[str] = Field(default_factory=list)
    source_count: int = 1
    source_diversity: float = Field(default=0, ge=0, le=1)
    recency_score: float = Field(default=0, ge=0, le=1)
    source_score: float = Field(default=0, ge=0, le=1)
    cross_source_score: float = Field(default=0, ge=0, le=1)
    personal_interest_score: float = Field(default=0, ge=0, le=1)
    novelty_score: float = Field(default=1, ge=0, le=1)
    llm_importance_score: float = Field(default=0, ge=0, le=1)
    global_importance_score: float = Field(default=0, ge=0, le=1)
    editorial_score: float = Field(default=0, ge=0, le=1)
    final_score: float = Field(default=0, ge=0, le=1)
    summary: str = ""
    why_it_matters: str = ""


class EventIntelligence(BaseModel):
    event_id: str
    summary_zh: str = Field(default="", max_length=800)
    why_it_matters: str = Field(default="", max_length=600)
    watch_next: str = Field(default="", max_length=400)
    confidence: float | None = Field(default=None, ge=0, le=1)
    key_facts: list[str] = Field(default_factory=list)
    source_consensus: str = Field(default="", max_length=300)


class EnrichedNewsEvent(BaseModel):
    event: NewsEvent
    intelligence: EventIntelligence


class EditorialSelection(BaseModel):
    candidate_count: int
    events: list[NewsEvent]
    selected_categories: dict[str, int] = Field(default_factory=dict)


class BriefingSection(BaseModel):
    name: str
    event_ids: list[str]
    text: str


class Briefing(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    generated_at: datetime = Field(default_factory=utc_now)
    event_count: int
    estimated_duration_seconds: int
    spoken_text: str
    sections: list[BriefingSection] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    is_fallback: bool = False


class PipelineMetrics(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: PipelineStatus = PipelineStatus.RUNNING
    sources_requested: int = 0
    sources_succeeded: int = 0
    sources_failed: int = 0
    items_fetched: int = 0
    items_after_dedup: int = 0
    events_clustered: int = 0
    events_shortlisted: int = 0
    candidate_events: int = 0
    selected_events: int = 0
    selected_categories: dict[str, int] = Field(default_factory=dict)
    llm_calls: int = 0
    llm_latency_ms: float = 0
    llm_synthesis_calls: int = 0
    llm_script_calls: int = 0
    llm_synthesis_latency_ms: float = 0
    llm_script_latency_ms: float = 0
    total_latency_ms: float = 0
    briefing_char_count: int = 0
    estimated_duration_seconds: int = 0
    fallback_used: bool = False
    fallback_reason: str = ""
    errors: list[str] = Field(default_factory=list)
