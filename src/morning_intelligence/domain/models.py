from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

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
    published_at: datetime | None
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
    background_zh: str = Field(default="", max_length=1000)
    why_it_matters: str = Field(default="", max_length=600)
    watch_next: str = Field(default="", max_length=400)
    confidence: float | None = Field(default=None, ge=0, le=1)
    key_facts: list[str] = Field(default_factory=list)
    source_consensus: str = Field(default="", max_length=300)


class GitHubTrendingRepo(BaseModel):
    rank: int = Field(ge=1, le=10)
    full_name: str
    url: str
    description: str = ""
    language: str | None = None
    total_stars: int | None = Field(default=None, ge=0)
    forks: int | None = Field(default=None, ge=0)
    stars_today: int | None = Field(default=None, ge=0)


class EmailMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=512)
    thread_id: str | None = Field(default=None, max_length=512)
    received_at: datetime
    sender: str = Field(min_length=1, max_length=1000)
    recipients: list[str] = Field(default_factory=list, max_length=100)
    cc: list[str] = Field(default_factory=list, max_length=100)
    subject: str = Field(default="", max_length=1000)
    snippet: str = Field(default="", max_length=2000)
    body_text: str = Field(default="", max_length=12_000)

    @field_validator("message_id", "sender", mode="before")
    @classmethod
    def required_identity_fields_must_not_be_blank(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("received_at")
    @classmethod
    def received_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value

    @field_validator("recipients", "cc")
    @classmethod
    def addresses_must_be_reasonable(cls, value: list[str]) -> list[str]:
        if any(len(address) > 1000 for address in value):
            raise ValueError("email address fields must not exceed 1000 characters")
        return value


class GitHubRepositoryIntelligence(BaseModel):
    repo: str
    summary_zh: str = Field(default="", max_length=500)
    why_it_matters: str = Field(default="", max_length=400)
    why_trending: str = Field(default="", max_length=300)
    main_technology: str = Field(default="", max_length=200)


class LLMUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cache_hit_tokens: int = Field(default=0, ge=0)
    cache_miss_tokens: int = Field(default=0, ge=0)


class EditorialBriefingOutput(BaseModel):
    events: list[EventIntelligence] = Field(default_factory=list)
    email_spoken_text: str = ""
    news_spoken_text: str = Field(
        default="", validation_alias=AliasChoices("news_spoken_text", "spoken_text")
    )
    github_repositories: list[GitHubRepositoryIntelligence] = Field(default_factory=list)

    @property
    def spoken_text(self) -> str:
        """V3-compatible read alias for callers that only know about the news script."""
        return self.news_spoken_text


class EditorialBriefingResult(BaseModel):
    output: EditorialBriefingOutput
    usage: LLMUsage = Field(default_factory=LLMUsage)


class LLMBudgetClaim(BaseModel):
    id: str
    purpose: str
    local_date: date
    claimed_at_utc: datetime
    completed_at_utc: datetime | None = None
    success: bool | None = None
    error: str = ""
    provider: str = ""
    model: str = ""


class LLMBudgetClaimResult(BaseModel):
    claimed: bool
    reason: str
    claim: LLMBudgetClaim | None = None


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
    news_window_start: datetime | None = None
    news_window_end: datetime | None = None
    github_trending_count: int = 0


class PipelineMetrics(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: PipelineStatus = PipelineStatus.RUNNING
    sources_requested: int = 0
    sources_succeeded: int = 0
    sources_failed: int = 0
    items_fetched: int = 0
    news_window_start_utc: datetime | None = None
    news_window_end_utc: datetime | None = None
    items_rejected_stale: int = 0
    items_rejected_missing_timestamp: int = 0
    items_rejected_future: int = 0
    fresh_items_count: int = 0
    github_trending_count: int = 0
    github_snapshot_date: date | None = None
    github_fetch_success: bool = False
    items_after_dedup: int = 0
    events_clustered: int = 0
    events_shortlisted: int = 0
    candidate_events: int = 0
    selected_events: int = 0
    selected_categories: dict[str, int] = Field(default_factory=dict)
    llm_calls: int = 0
    llm_editorial_calls: int = 0
    llm_latency_ms: float = 0
    llm_synthesis_calls: int = 0
    llm_script_calls: int = 0
    llm_synthesis_latency_ms: float = 0
    llm_script_latency_ms: float = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    total_latency_ms: float = 0
    briefing_char_count: int = 0
    estimated_duration_seconds: int = 0
    fallback_used: bool = False
    fallback_reason: str = ""
    errors: list[str] = Field(default_factory=list)
