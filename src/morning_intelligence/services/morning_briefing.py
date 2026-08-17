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
    EditorialSelector,
    cluster_items,
    deduplicate_items,
    normalize_item,
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
        self.editorial_selector = EditorialSelector(preferences)
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

                candidates = sorted(
                    (
                        event
                        for event in events
                        if event.final_score >= self.preferences.briefing.min_event_score
                    ),
                    key=lambda event: event.final_score,
                    reverse=True,
                )[
                    : self.preferences.briefing.max_events * 4
                ]
                metrics.candidate_events = len(candidates)
                selection = self.editorial_selector.select(candidates)
                selected = selection.events
                metrics.selected_events = len(selected)
                metrics.events_shortlisted = len(selected)
                metrics.selected_categories = selection.selected_categories
                self.repository.save_items(normalized)
                self.repository.save_events(selected)

                generation = await self.generator.generate(
                    selected,
                    target_minutes=self.preferences.briefing.target_minutes,
                    language=self.preferences.language,
                    characters_per_minute=self.preferences.briefing.characters_per_minute,
                )
                briefing = generation.briefing
                metrics.llm_synthesis_calls = generation.synthesis_calls
                metrics.llm_script_calls = generation.script_calls
                metrics.llm_calls = generation.synthesis_calls + generation.script_calls
                metrics.llm_synthesis_latency_ms = generation.synthesis_latency_ms
                metrics.llm_script_latency_ms = generation.script_latency_ms
                metrics.llm_latency_ms = (
                    generation.synthesis_latency_ms + generation.script_latency_ms
                )
                metrics.fallback_used = generation.fallback_used
                metrics.fallback_reason = generation.fallback_reason
                if generation.fallback_used and self.llm_provider is not None:
                    metrics.errors.append(
                        f"LLM editorial fallback: {generation.fallback_reason}"[:500]
                    )
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
                    "candidates=%d selected=%d categories=%s llm_synthesis_calls=%d "
                    "llm_script_calls=%d fallback=%s total_latency_ms=%.1f",
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
                    metrics.llm_synthesis_calls,
                    metrics.llm_script_calls,
                    metrics.fallback_reason or "none",
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

        generation = await self.generator.generate(
            events,
            target_minutes=target_minutes or self.preferences.briefing.target_minutes,
            language=self.preferences.language,
            characters_per_minute=self.preferences.briefing.characters_per_minute,
        )
        briefing = generation.briefing
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
