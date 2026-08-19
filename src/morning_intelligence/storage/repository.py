from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Protocol

from morning_intelligence.domain import (
    Briefing,
    GitHubTrendingRepo,
    LLMBudgetClaimResult,
    NewsEvent,
    NewsItem,
    PipelineMetrics,
)


class BriefingRepository(Protocol):
    def initialize(self) -> None: ...
    def save_items(self, items: list[NewsItem]) -> None: ...
    def save_events(self, events: list[NewsEvent]) -> None: ...
    def get_latest_events(self) -> tuple[list[NewsEvent], datetime | None]: ...
    def save_briefing(self, briefing: Briefing) -> None: ...
    def get_latest_briefing(self) -> Briefing | None: ...
    def save_pipeline_run(self, metrics: PipelineMetrics) -> None: ...
    def recent_spoken_titles(self, days: int) -> list[str]: ...
    def save_github_trending_snapshot(
        self,
        snapshot_date: date,
        repositories: list[GitHubTrendingRepo],
        *,
        fetched_at_utc: datetime,
    ) -> None: ...
    def get_github_trending_snapshot(self, snapshot_date: date) -> list[GitHubTrendingRepo]: ...
    def claim_llm_budget(
        self,
        *,
        purpose: str,
        local_date: date,
        now_utc: datetime,
        minimum_interval: timedelta,
        provider: str,
        model: str,
    ) -> LLMBudgetClaimResult: ...
    def complete_llm_budget_claim(
        self,
        claim_id: str,
        *,
        success: bool,
        error: str,
        completed_at_utc: datetime,
    ) -> None: ...
