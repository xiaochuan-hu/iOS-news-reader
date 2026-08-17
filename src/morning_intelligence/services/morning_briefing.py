from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from morning_intelligence.briefing import BriefingGenerator
from morning_intelligence.config import PreferencesConfig, Settings
from morning_intelligence.domain import Briefing, PipelineMetrics, PipelineStatus
from morning_intelligence.llm import LLMProvider
from morning_intelligence.processing import (
    calculate_final_score,
    cluster_items,
    deduplicate_items,
    normalize_item,
    rank_events,
    score_event,
    title_similarity,
)
from morning_intelligence.storage import BriefingRepository

from .collector import CollectorService

logger = logging.getLogger(__name__)


class MorningBriefingService:
    def __init__(
        self,
        *,
        settings: Settings,
        preferences: PreferencesConfig,
        repository: BriefingRepository,
        collector: CollectorService,
        llm_provider: LLMProvider | None,
    ) -> None:
        self.settings = settings
        self.preferences = preferences
        self.repository = repository
        self.collector = collector
        self.llm_provider = llm_provider
        self.generator = BriefingGenerator(llm_provider)
        self._refresh_lock = asyncio.Lock()

    async def refresh(self) -> Briefing:
        async with self._refresh_lock:
            metrics = PipelineMetrics(sources_requested=len(self.collector.sources))
            started = time.perf_counter()
            try:
                since = datetime.now(UTC) - timedelta(
                    hours=self.preferences.processing.lookback_hours
                )
                collected = await self.collector.collect(since)
                metrics.sources_succeeded = sum(o.succeeded for o in collected.outcomes)
                metrics.sources_failed = sum(not o.succeeded for o in collected.outcomes)
                metrics.errors = [o.error for o in collected.outcomes if o.error]
                metrics.items_fetched = len(collected.items)
                if collected.all_failed or not collected.outcomes:
                    raise RuntimeError("all configured news sources failed")

                normalized = [normalize_item(item) for item in collected.items]
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
                    score_event(event, self.preferences, was_recently_spoken=repeated)

                candidates = sorted(events, key=lambda event: event.final_score, reverse=True)[
                    : self.preferences.briefing.max_events * 2
                ]
                if self.llm_provider and candidates:
                    call_started = time.perf_counter()
                    metrics.llm_calls += 1
                    try:
                        scores = await self.llm_provider.score_events(candidates)
                        for event in candidates:
                            event.llm_importance_score = scores.get(event.id, 0.0)
                            event.final_score = calculate_final_score(event, self.preferences)
                    except Exception as exc:
                        metrics.errors.append(f"LLM scoring: {type(exc).__name__}: {exc}"[:500])
                    metrics.llm_latency_ms += (time.perf_counter() - call_started) * 1000

                ranked = rank_events(candidates, self.preferences)
                metrics.events_shortlisted = len(ranked)
                self.repository.save_items(normalized)
                self.repository.save_events(ranked)

                if self.llm_provider and ranked:
                    metrics.llm_calls += 1
                    generation_started = time.perf_counter()
                else:
                    generation_started = 0.0
                briefing, llm_used = await self.generator.generate(
                    ranked,
                    target_minutes=self.preferences.briefing.target_minutes,
                    language=self.preferences.language,
                )
                if generation_started:
                    metrics.llm_latency_ms += (
                        time.perf_counter() - generation_started
                    ) * 1000
                    if not llm_used:
                        metrics.errors.append("LLM synthesis failed; deterministic script used")
                self.repository.save_briefing(briefing)

                metrics.status = (
                    PipelineStatus.PARTIAL if metrics.errors else PipelineStatus.SUCCEEDED
                )
                metrics.briefing_char_count = len(briefing.spoken_text)
                metrics.estimated_duration_seconds = briefing.estimated_duration_seconds
                return briefing
            except Exception as exc:
                metrics.status = PipelineStatus.FAILED
                metrics.errors.append(f"{type(exc).__name__}: {exc}"[:500])
                cached = self.repository.get_latest_briefing()
                if cached is not None:
                    return cached.model_copy(update={"is_fallback": True})
                raise
            finally:
                metrics.completed_at = datetime.now(UTC)
                metrics.total_latency_ms = (time.perf_counter() - started) * 1000
                self.repository.save_pipeline_run(metrics)
                logger.info(
                    "pipeline_run run_id=%s status=%s sources=%d/%d items=%d events=%d "
                    "shortlisted=%d llm_calls=%d total_latency_ms=%.1f",
                    metrics.run_id,
                    metrics.status,
                    metrics.sources_succeeded,
                    metrics.sources_requested,
                    metrics.items_fetched,
                    metrics.events_clustered,
                    metrics.events_shortlisted,
                    metrics.llm_calls,
                    metrics.total_latency_ms,
                )

    async def latest_or_generate(self) -> Briefing:
        cached = self.repository.get_latest_briefing()
        return cached if cached is not None else await self._deadline_refresh()

    async def generate(
        self,
        *,
        target_minutes: int | None,
        incremental_refresh: bool,
        max_age_minutes: int,
    ) -> Briefing:
        events, saved_at = self.repository.get_latest_events()
        is_fresh = bool(
            saved_at
            and datetime.now(UTC) - saved_at <= timedelta(minutes=max_age_minutes)
        )
        if incremental_refresh and not is_fresh:
            return await self._deadline_refresh()
        if not events:
            return await self._deadline_refresh()

        briefing, _ = await self.generator.generate(
            events,
            target_minutes=target_minutes or self.preferences.briefing.target_minutes,
            language=self.preferences.language,
        )
        self.repository.save_briefing(briefing)
        return briefing

    async def _deadline_refresh(self) -> Briefing:
        try:
            async with asyncio.timeout(self.settings.morning_generation_deadline_seconds):
                return await self.refresh()
        except TimeoutError:
            cached = self.repository.get_latest_briefing()
            if cached is not None:
                return cached.model_copy(update={"is_fallback": True})
            raise
