from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from morning_intelligence.briefing import BriefingGenerator
from morning_intelligence.config import PreferencesConfig, Settings
from morning_intelligence.domain import (
    Briefing,
    GitHubTrendingRepo,
    GitHubTrendingSource,
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
from morning_intelligence.tts import AudioService

from .collector import CollectorService

logger = logging.getLogger(__name__)

EDITORIAL_BUDGET_PURPOSE = "morning_editorial"
GENERATION_DEADLINE_EXCEEDED = "generation_deadline_exceeded"
REFRESH_CANCELLED = "refresh_cancelled"
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


@dataclass(frozen=True)
class GitHubTrendingSelection:
    repositories: list[GitHubTrendingRepo]
    source: GitHubTrendingSource
    snapshot_date: date | None


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
        audio_service: AudioService | None = None,
    ) -> None:
        self.settings = settings
        self.preferences = preferences
        self.repository = repository
        self.collector = collector
        self.llm_provider = llm_provider
        self.github_provider = github_provider
        self.audio_service = audio_service
        self.generator = BriefingGenerator(llm_provider)
        self.editorial_selector = EditorialSelector(preferences)
        # Optimization only. SQLite BEGIN IMMEDIATE is the cross-process correctness lock.
        self._refresh_lock = asyncio.Lock()

    def latest(self) -> Briefing | None:
        """Cache-only read. This method must never collect sources or invoke an LLM."""
        return self.repository.get_latest_briefing()

    def recent(self, limit: int) -> list[Briefing]:
        """Return persisted briefings without collecting sources or invoking an LLM."""
        return self.repository.get_recent_briefings(limit)

    async def daily_refresh(
        self, local_date: date, *, now_utc: datetime | None = None
    ) -> DailyRefreshResult:
        async with self._refresh_lock:
            return await self._daily_refresh_locked(
                local_date, now_utc=(now_utc or datetime.now(UTC))
            )

    async def deadline_daily_refresh(self, local_date: date) -> DailyRefreshResult:
        refresh_task = asyncio.create_task(self.daily_refresh(local_date))
        try:
            async with asyncio.timeout(self.settings.morning_generation_deadline_seconds):
                return await asyncio.shield(refresh_task)
        except TimeoutError as exc:
            refresh_task.cancel(GENERATION_DEADLINE_EXCEEDED)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await refresh_task
            cached = self.repository.get_latest_briefing()
            logger.warning(
                "daily_refresh_deadline local_date=%s reason=%s cached=%s",
                local_date.isoformat(),
                GENERATION_DEADLINE_EXCEEDED,
                cached is not None,
            )
            if cached is not None:
                return DailyRefreshResult(
                    "cached", local_date, cached, GENERATION_DEADLINE_EXCEEDED
                )
            raise TimeoutError(GENERATION_DEADLINE_EXCEEDED) from exc
        except asyncio.CancelledError:
            refresh_task.cancel(REFRESH_CANCELLED)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await refresh_task
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
        stage_timings = {
            "source_collection_ms": 0.0,
            "github_ms": 0.0,
            "freshness_normalize_dedup_ms": 0.0,
            "clustering_ms": 0.0,
            "scoring_selection_ms": 0.0,
            "llm_editorial_ms": 0.0,
            "persistence_ms": 0.0,
        }
        selected = []
        github_selection = GitHubTrendingSelection(
            [], GitHubTrendingSource.UNAVAILABLE, None
        )
        claim_id = ""
        try:
            if now_utc.tzinfo is None:
                raise ValueError("now_utc must be timezone-aware")
            window_end_utc = now_utc.astimezone(UTC)
            window_start_utc = window_end_utc - timedelta(hours=24)
            metrics.news_window_start_utc = window_start_utc
            metrics.news_window_end_utc = window_end_utc

            collection_task = asyncio.create_task(
                _measure_async_stage(
                    self.collector.collect(window_start_utc),
                    stage="source_collection_ms",
                    timings=stage_timings,
                )
            )
            github_task = (
                asyncio.create_task(
                    _measure_async_stage(
                        self.github_provider.fetch_today(),
                        stage="github_ms",
                        timings=stage_timings,
                    )
                )
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
            github_selection = await self._github_repositories(
                github_task, local_date=local_date, now_utc=window_end_utc, metrics=metrics
            )
            metrics.sources_succeeded = sum(o.succeeded for o in collected.outcomes)
            metrics.sources_failed = sum(not o.succeeded for o in collected.outcomes)
            metrics.errors.extend(o.error for o in collected.outcomes if o.error)
            metrics.items_fetched = len(collected.items)
            if collected.all_failed or not collected.outcomes:
                raise RuntimeError("all configured news sources failed")

            stage_started = time.perf_counter()
            try:
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
            finally:
                stage_timings["freshness_normalize_dedup_ms"] = (
                    time.perf_counter() - stage_started
                ) * 1000

            stage_started = time.perf_counter()
            events = cluster_items(
                deduplicated,
                threshold=self.preferences.processing.fuzzy_title_threshold,
            )
            metrics.events_clustered = len(events)
            stage_timings["clustering_ms"] = (time.perf_counter() - stage_started) * 1000

            stage_started = time.perf_counter()
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

            candidate_pool_size = max(
                self.preferences.briefing.max_events,
                self.preferences.editorial.ai_top_items
                + self.preferences.editorial.general_top_items,
            ) * 4
            candidates = sorted(
                (
                    event
                    for event in events
                    if event.final_score >= self.preferences.briefing.min_event_score
                ),
                key=lambda event: event.final_score,
                reverse=True,
            )[:candidate_pool_size]
            metrics.candidate_events = len(candidates)
            selection = self.editorial_selector.select(candidates)
            selected = selection.events
            metrics.selected_events = len(selected)
            metrics.events_shortlisted = len(selected)
            metrics.selected_categories = selection.selected_categories
            stage_timings["scoring_selection_ms"] = (
                time.perf_counter() - stage_started
            ) * 1000

            persistence_started = time.perf_counter()
            self.repository.save_items(normalized)
            self.repository.save_events(selected)
            stage_timings["persistence_ms"] += (
                time.perf_counter() - persistence_started
            ) * 1000

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

            stage_started = time.perf_counter()
            try:
                generation = await self.generator.generate(
                    selected,
                    target_minutes=self.preferences.briefing.target_minutes,
                    language=self.preferences.language,
                    characters_per_minute=self.preferences.briefing.characters_per_minute,
                    local_date=local_date,
                    github_repositories=github_selection.repositories,
                    github_source=github_selection.source,
                    news_window_start=window_start_utc,
                    news_window_end=window_end_utc,
                )
            finally:
                stage_timings["llm_editorial_ms"] = (
                    time.perf_counter() - stage_started
                ) * 1000
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
            persistence_started = time.perf_counter()
            self.repository.save_briefing(briefing)
            self._schedule_audio(briefing)
            stage_timings["persistence_ms"] += (
                time.perf_counter() - persistence_started
            ) * 1000
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
        except asyncio.CancelledError as exc:
            cancellation_reason = (
                str(exc) if str(exc) in {GENERATION_DEADLINE_EXCEEDED, REFRESH_CANCELLED}
                else REFRESH_CANCELLED
            )
            metrics.status = PipelineStatus.FAILED
            metrics.errors.append(cancellation_reason)
            metrics.fallback_reason = cancellation_reason
            if claim_id:
                try:
                    self.repository.complete_llm_budget_claim(
                        claim_id,
                        success=False,
                        error=cancellation_reason,
                        completed_at_utc=datetime.now(UTC),
                    )
                except Exception:
                    logger.exception(
                        "failed to complete cancelled LLM budget claim claim_id=%s",
                        claim_id,
                    )
            logger.warning(
                "daily_refresh_cancelled run_id=%s reason=%s claim_consumed=%s",
                metrics.run_id,
                cancellation_reason,
                bool(claim_id),
            )
            raise
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
                        github_repositories=github_selection.repositories,
                        github_source=github_selection.source,
                        news_window_start=metrics.news_window_start_utc,
                        news_window_end=metrics.news_window_end_utc,
                    )
                    persistence_started = time.perf_counter()
                    self.repository.save_briefing(fallback.briefing)
                    self._schedule_audio(fallback.briefing)
                    stage_timings["persistence_ms"] += (
                        time.perf_counter() - persistence_started
                    ) * 1000
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
                "pipeline_stage_timings run_id=%s source_collection_ms=%.1f "
                "github_ms=%.1f freshness_normalize_dedup_ms=%.1f clustering_ms=%.1f "
                "scoring_selection_ms=%.1f llm_editorial_ms=%.1f persistence_ms=%.1f "
                "total_latency_ms=%.1f",
                metrics.run_id,
                stage_timings["source_collection_ms"],
                stage_timings["github_ms"],
                stage_timings["freshness_normalize_dedup_ms"],
                stage_timings["clustering_ms"],
                stage_timings["scoring_selection_ms"],
                stage_timings["llm_editorial_ms"],
                stage_timings["persistence_ms"],
                metrics.total_latency_ms,
            )
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
    def _schedule_audio(self, briefing: Briefing) -> None:
        if self.audio_service is None:
            return
        try:
            self.audio_service.schedule(briefing.spoken_text)
        except Exception as exc:
            logger.warning("could not schedule background TTS generation: %s", exc)

    async def _github_repositories(
        self,
        task: asyncio.Task[list[GitHubTrendingRepo]] | None,
        *,
        local_date: date,
        now_utc: datetime,
        metrics: PipelineMetrics,
    ) -> GitHubTrendingSelection:
        live_repositories: list[GitHubTrendingRepo] = []
        live_error = ""
        if task is not None:
            try:
                live_repositories = await task
                metrics.github_fetch_success = bool(live_repositories)
            except Exception as exc:
                live_error = f"github:{type(exc).__name__}: {exc}"[:500]

        if _is_complete_github_snapshot(live_repositories):
            self.repository.save_github_trending_snapshot(
                local_date, live_repositories, fetched_at_utc=now_utc
            )
            selection = GitHubTrendingSelection(
                live_repositories, GitHubTrendingSource.LIVE_FULL, local_date
            )
        elif len(live_repositories) >= 5:
            selection = GitHubTrendingSelection(
                live_repositories, GitHubTrendingSource.LIVE_PARTIAL, local_date
            )
        else:
            cached = self._recent_complete_github_snapshot(local_date)
            if cached is not None:
                snapshot_date, repositories = cached
                selection = GitHubTrendingSelection(
                    repositories,
                    GitHubTrendingSource.CACHED_FALLBACK,
                    snapshot_date,
                )
            elif live_repositories:
                selection = GitHubTrendingSelection(
                    live_repositories, GitHubTrendingSource.LIVE_PARTIAL, local_date
                )
            else:
                selection = GitHubTrendingSelection(
                    [], GitHubTrendingSource.UNAVAILABLE, None
                )

        if selection.source == GitHubTrendingSource.UNAVAILABLE:
            if not live_error and task is not None:
                live_error = "github: no valid repositories parsed"
            if live_error:
                metrics.errors.append(live_error)
        elif live_error:
            logger.warning("github_trending live_fetch_error=%s", live_error)

        metrics.github_trending_count = len(selection.repositories)
        metrics.github_snapshot_date = selection.snapshot_date
        logger.info(
            "github_trending source=%s live_count=%d included_count=%d "
            "snapshot_date=%s fetch_success=%s",
            selection.source,
            len(live_repositories),
            metrics.github_trending_count,
            selection.snapshot_date.isoformat() if selection.snapshot_date else "none",
            metrics.github_fetch_success,
        )
        return selection

    def _recent_complete_github_snapshot(
        self, local_date: date
    ) -> tuple[date, list[GitHubTrendingRepo]] | None:
        for days_ago in range(3):
            snapshot_date = local_date - timedelta(days=days_ago)
            repositories = self.repository.get_github_trending_snapshot(snapshot_date)
            if _is_complete_github_snapshot(repositories):
                return snapshot_date, repositories
        return None


async def _measure_async_stage[TimedResult](
    awaitable: Awaitable[TimedResult],
    *,
    stage: str,
    timings: dict[str, float],
) -> TimedResult:
    started = time.perf_counter()
    try:
        return await awaitable
    finally:
        timings[stage] = (time.perf_counter() - started) * 1000


def _is_complete_github_snapshot(repositories: list[GitHubTrendingRepo]) -> bool:
    if len(repositories) != 10:
        return False
    ranks = {repository.rank for repository in repositories}
    identities = {
        repository.full_name.casefold()
        for repository in repositories
        if re.fullmatch(r"[^/\s]+/[^/\s]+", repository.full_name)
    }
    return ranks == set(range(1, 11)) and len(identities) == 10


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
