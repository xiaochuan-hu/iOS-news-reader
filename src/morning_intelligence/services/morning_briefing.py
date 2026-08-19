from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from morning_intelligence.briefing import BriefingGenerator
from morning_intelligence.config import PreferencesConfig, Settings
from morning_intelligence.domain import (
    Briefing,
    GitHubTrendingRepo,
    NewsItem,
    PipelineMetrics,
    PipelineStatus,
)
from morning_intelligence.llm import LLMProvider
from morning_intelligence.processing import (
    EditorialSelector,
    cluster_items,
    deduplicate_items,
    normalize_item,
    score_event,
    title_similarity,
)
from morning_intelligence.providers import GitHubTrendingProvider
from morning_intelligence.storage import BriefingRepository

from .collector import CollectorService

logger = logging.getLogger(__name__)

EDITORIAL_BUDGET_PURPOSE = "morning_editorial"
DailyRefreshStatus = Literal[
    "generated",
    "generated_fallback",
    "already_claimed",
    "no_llm_available",
    "cached",
]


@dataclass(frozen=True)
class DailyRefreshResult:
    status: DailyRefreshStatus
    local_date: date
    briefing: Briefing | None
    budget_reason: str = ""


@dataclass(frozen=True)
class FreshnessFilterResult:
    items: list[NewsItem]
    stale_rejected: int
    missing_timestamp_rejected: int
    future_rejected: int


class MorningBriefingService:
    def __init__(
        self,
        *,
        settings: Settings,
        preferences: PreferencesConfig,
        repository: BriefingRepository,
        collector: CollectorService,
        llm_provider: LLMProvider | None,
        github_provider: GitHubTrendingProvider | None = None,
    ) -> None:
        self.settings = settings
        self.preferences = preferences
        self.repository = repository
        self.collector = collector
        self.llm_provider = llm_provider
        self.github_provider = github_provider
        self.generator = BriefingGenerator(llm_provider)
        self.editorial_selector = EditorialSelector(preferences)
        # Optimization only. SQLite BEGIN IMMEDIATE is the cross-process correctness lock.
        self._refresh_lock = asyncio.Lock()

    def latest(self) -> Briefing | None:
        """Cache-only read. This method must never collect sources or invoke an LLM."""
        return self.repository.get_latest_briefing()

    async def daily_refresh(
        self, local_date: date, *, now_utc: datetime | None = None
    ) -> DailyRefreshResult:
        async with self._refresh_lock:
            return await self._daily_refresh_locked(
                local_date, now_utc=(now_utc or datetime.now(UTC))
            )

    async def deadline_daily_refresh(self, local_date: date) -> DailyRefreshResult:
        try:
            async with asyncio.timeout(self.settings.morning_generation_deadline_seconds):
                return await self.daily_refresh(local_date)
        except TimeoutError:
            cached = self.repository.get_latest_briefing()
            if cached is not None:
                return DailyRefreshResult("cached", local_date, cached, "deadline")
            raise

    async def generate(
        self,
        *,
        target_minutes: int | None,
        incremental_refresh: bool,
        max_age_minutes: int,
    ) -> Briefing:
        """Legacy manual entry point; it deliberately has no budget bypass."""
        del target_minutes, incremental_refresh, max_age_minutes
        result = await self.deadline_daily_refresh(datetime.now(UTC).date())
        briefing = result.briefing or self.repository.get_latest_briefing()
        if briefing is None:
            raise RuntimeError("briefing has not been generated yet")
        return briefing

    async def _daily_refresh_locked(
        self, local_date: date, *, now_utc: datetime
    ) -> DailyRefreshResult:
        metrics = PipelineMetrics(sources_requested=len(self.collector.sources))
        started = time.perf_counter()
        selected = []
        claim_id = ""
        try:
            if now_utc.tzinfo is None:
                raise ValueError("now_utc must be timezone-aware")
            window_end_utc = now_utc.astimezone(UTC)
            window_start_utc = window_end_utc - timedelta(hours=24)
            metrics.news_window_start_utc = window_start_utc
            metrics.news_window_end_utc = window_end_utc

            collection_task = asyncio.create_task(self.collector.collect(window_start_utc))
            github_task = (
                asyncio.create_task(self.github_provider.fetch_today())
                if self.github_provider is not None
                else None
            )
            try:
                collected = await collection_task
            except BaseException:
                if github_task is not None:
                    github_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await github_task
                raise
            github_repositories = await self._github_repositories(
                github_task, local_date=local_date, now_utc=window_end_utc, metrics=metrics
            )
            metrics.sources_succeeded = sum(o.succeeded for o in collected.outcomes)
            metrics.sources_failed = sum(not o.succeeded for o in collected.outcomes)
            metrics.errors.extend(o.error for o in collected.outcomes if o.error)
            metrics.items_fetched = len(collected.items)
            if collected.all_failed or not collected.outcomes:
                raise RuntimeError("all configured news sources failed")

            freshness = filter_fresh_news_items(
                collected.items,
                window_start_utc=window_start_utc,
                window_end_utc=window_end_utc,
            )
            metrics.items_rejected_stale = freshness.stale_rejected
            metrics.items_rejected_missing_timestamp = freshness.missing_timestamp_rejected
            metrics.items_rejected_future = freshness.future_rejected
            metrics.fresh_items_count = len(freshness.items)
            normalized = [normalize_item(item) for item in freshness.items]
            deduplicated = deduplicate_items(normalized)
            metrics.items_after_dedup = len(deduplicated)
            events = cluster_items(
                deduplicated,
                threshold=self.preferences.processing.fuzzy_title_threshold,
            )
            metrics.events_clustered = len(events)

            recent_titles = self.repository.recent_spoken_titles(
                self.preferences.processing.novelty_lookback_days
            )
            for event in events:
                repeated = any(
                    title_similarity(event.canonical_title, title) >= 0.82
                    for title in recent_titles
                )
                score_event(
                    event,
                    self.preferences,
                    now=now_utc,
                    was_recently_spoken=repeated,
                )

            candidates = sorted(
                (
                    event
                    for event in events
                    if event.final_score >= self.preferences.briefing.min_event_score
                ),
                key=lambda event: event.final_score,
                reverse=True,
            )[: self.preferences.briefing.max_events * 4]
            metrics.candidate_events = len(candidates)
            selection = self.editorial_selector.select(candidates)
            selected = selection.events
            metrics.selected_events = len(selected)
            metrics.events_shortlisted = len(selected)
            metrics.selected_categories = selection.selected_categories
            self.repository.save_items(normalized)
            self.repository.save_events(selected)

            if self.llm_provider is not None and selected:
                claim_result = self.repository.claim_llm_budget(
                    purpose=EDITORIAL_BUDGET_PURPOSE,
                    local_date=local_date,
                    now_utc=now_utc,
                    minimum_interval=timedelta(
                        hours=self.settings.morning_min_llm_interval_hours
                    ),
                    provider="openai_compatible",
                    model=self.settings.llm_model,
                )
                if not claim_result.claimed:
                    metrics.status = PipelineStatus.SUCCEEDED
                    logger.info(
                        "daily_llm_budget local_date=%s claimed=false reason=%s llm_calls=0",
                        local_date.isoformat(),
                        claim_result.reason,
                    )
                    return DailyRefreshResult(
                        "already_claimed",
                        local_date,
                        self.repository.get_latest_briefing(),
                        claim_result.reason,
                    )
                if claim_result.claim is None:
                    raise RuntimeError("budget claim succeeded without a claim record")
                claim_id = claim_result.claim.id
                logger.info(
                    "daily_llm_budget local_date=%s claimed=true reason=claimed",
                    local_date.isoformat(),
                )

            generation = await self.generator.generate(
                selected,
                target_minutes=self.preferences.briefing.target_minutes,
                language=self.preferences.language,
                characters_per_minute=self.preferences.briefing.characters_per_minute,
                local_date=local_date,
                github_repositories=github_repositories,
                news_window_start=window_start_utc,
                news_window_end=window_end_utc,
            )
            briefing = generation.briefing
            metrics.llm_calls = generation.editorial_calls
            metrics.llm_editorial_calls = generation.editorial_calls
            metrics.llm_latency_ms = generation.editorial_latency_ms
            # Retain V2 fields for persisted-metrics readers; the V3 combined path leaves both zero.
            metrics.llm_synthesis_calls = 0
            metrics.llm_script_calls = 0
            metrics.prompt_tokens = generation.usage.prompt_tokens
            metrics.completion_tokens = generation.usage.completion_tokens
            metrics.total_tokens = generation.usage.total_tokens
            metrics.cache_hit_tokens = generation.usage.cache_hit_tokens
            metrics.cache_miss_tokens = generation.usage.cache_miss_tokens
            metrics.fallback_used = generation.fallback_used
            metrics.fallback_reason = generation.fallback_reason

            if claim_id:
                self.repository.complete_llm_budget_claim(
                    claim_id,
                    success=generation.llm_succeeded,
                    error=generation.fallback_reason,
                    completed_at_utc=datetime.now(UTC),
                )
            if generation.fallback_used and self.llm_provider is not None:
                metrics.errors.append(
                    f"LLM editorial fallback: {generation.fallback_reason}"[:500]
                )

            # INSERT happens only after a complete briefing exists; the prior cache stays readable.
            self.repository.save_briefing(briefing)
            metrics.status = (
                PipelineStatus.PARTIAL if metrics.errors else PipelineStatus.SUCCEEDED
            )
            metrics.briefing_char_count = len(briefing.spoken_text)
            metrics.estimated_duration_seconds = briefing.estimated_duration_seconds
            if self.llm_provider is None:
                status: DailyRefreshStatus = "no_llm_available"
            elif not selected or generation.fallback_used:
                status = "generated_fallback"
            else:
                status = "generated"
            return DailyRefreshResult(status, local_date, briefing)
        except Exception as exc:
            metrics.status = PipelineStatus.FAILED
            metrics.errors.append(f"{type(exc).__name__}: {exc}"[:500])
            if claim_id:
                self.repository.complete_llm_budget_claim(
                    claim_id,
                    success=False,
                    error=f"application:{type(exc).__name__}",
                    completed_at_utc=datetime.now(UTC),
                )
                try:
                    fallback = await BriefingGenerator().generate(
                        selected,
                        target_minutes=self.preferences.briefing.target_minutes,
                        language=self.preferences.language,
                        characters_per_minute=self.preferences.briefing.characters_per_minute,
                        local_date=local_date,
                        github_repositories=(
                            self.repository.get_github_trending_snapshot(local_date)
                        ),
                        news_window_start=metrics.news_window_start_utc,
                        news_window_end=metrics.news_window_end_utc,
                    )
                    self.repository.save_briefing(fallback.briefing)
                    metrics.fallback_used = True
                    metrics.fallback_reason = f"application:{type(exc).__name__}"
                    return DailyRefreshResult(
                        "generated_fallback", local_date, fallback.briefing
                    )
                except Exception:
                    logger.exception("deterministic fallback failed after LLM budget claim")
            cached = self.repository.get_latest_briefing()
            if cached is not None:
                return DailyRefreshResult(
                    "cached",
                    local_date,
                    cached.model_copy(update={"is_fallback": True}),
                    f"pipeline:{type(exc).__name__}",
                )
            raise
        finally:
            metrics.completed_at = datetime.now(UTC)
            metrics.total_latency_ms = (time.perf_counter() - started) * 1000
            self.repository.save_pipeline_run(metrics)
            logger.info(
                "pipeline_run run_id=%s status=%s sources=%d/%d items=%d events=%d "
                "candidates=%d selected=%d categories=%s llm_calls=%d "
                "llm_editorial_calls=%d fallback=%s prompt_tokens=%d "
                "completion_tokens=%d freshness_window_hours=24 fresh_items=%d "
                "stale_rejected=%d missing_timestamp_rejected=%d future_rejected=%d "
                "github_count=%d github_fetch_success=%s total_latency_ms=%.1f",
                metrics.run_id,
                metrics.status,
                metrics.sources_succeeded,
                metrics.sources_requested,
                metrics.items_fetched,
                metrics.events_clustered,
                metrics.candidate_events,
                metrics.selected_events,
                ",".join(
                    f"{category}:{count}"
                    for category, count in sorted(metrics.selected_categories.items())
                ),
                metrics.llm_calls,
                metrics.llm_editorial_calls,
                metrics.fallback_reason or "none",
                metrics.prompt_tokens,
                metrics.completion_tokens,
                metrics.fresh_items_count,
                metrics.items_rejected_stale,
                metrics.items_rejected_missing_timestamp,
                metrics.items_rejected_future,
                metrics.github_trending_count,
                metrics.github_fetch_success,
                metrics.total_latency_ms,
            )

    async def _github_repositories(
        self,
        task: asyncio.Task[list[GitHubTrendingRepo]] | None,
        *,
        local_date: date,
        now_utc: datetime,
        metrics: PipelineMetrics,
    ) -> list[GitHubTrendingRepo]:
        repositories: list[GitHubTrendingRepo] = []
        if task is not None:
            try:
                repositories = await task
                self.repository.save_github_trending_snapshot(
                    local_date, repositories, fetched_at_utc=now_utc
                )
                metrics.github_fetch_success = True
            except Exception as exc:
                metrics.errors.append(f"github:{type(exc).__name__}: {exc}"[:500])
        if not repositories:
            repositories = self.repository.get_github_trending_snapshot(local_date)
        metrics.github_trending_count = len(repositories)
        metrics.github_snapshot_date = local_date if repositories else None
        return repositories


def filter_fresh_news_items(
    items: list[NewsItem], *, window_start_utc: datetime, window_end_utc: datetime
) -> FreshnessFilterResult:
    """Authoritative inclusive rolling-window filter applied before normalization."""
    if window_start_utc.tzinfo is None or window_end_utc.tzinfo is None:
        raise ValueError("freshness window must be timezone-aware")
    fresh: list[NewsItem] = []
    stale = missing = future = 0
    for item in items:
        published = item.published_at
        if published is None or published.tzinfo is None:
            missing += 1
            continue
        published_utc = published.astimezone(UTC)
        if published_utc > window_end_utc:
            future += 1
        elif published_utc < window_start_utc:
            stale += 1
        else:
            fresh.append(item.model_copy(update={"published_at": published_utc}))
    return FreshnessFilterResult(fresh, stale, missing, future)
