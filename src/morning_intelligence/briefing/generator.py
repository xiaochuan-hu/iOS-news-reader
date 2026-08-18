from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from morning_intelligence.domain import (
    Briefing,
    BriefingSection,
    EnrichedNewsEvent,
    EventIntelligence,
    LLMUsage,
    NewsEvent,
)
from morning_intelligence.llm import LLMProvider

from .spoken_script import (
    deterministic_event_intelligence,
    deterministic_spoken_script,
    estimate_duration_seconds,
    sanitize_spoken_text,
)


@dataclass(frozen=True)
class BriefingGenerationResult:
    briefing: Briefing
    editorial_calls: int = 0
    editorial_latency_ms: float = 0.0
    usage: LLMUsage = field(default_factory=LLMUsage)
    llm_succeeded: bool = False
    fallback_used: bool = False
    fallback_reason: str = ""


class BriefingGenerator:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    async def generate(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int = 260,
    ) -> BriefingGenerationResult:
        now = datetime.now(UTC)
        enriched = _fallback_enrichment(events)
        editorial_calls = 0
        editorial_latency_ms = 0.0
        usage = LLMUsage()
        llm_succeeded = False
        fallback_reason = ""
        spoken_text = ""

        if self.provider and events:
            editorial_calls = 1
            started = time.perf_counter()
            try:
                generated = await self.provider.generate_editorial_briefing(
                    events,
                    target_minutes=target_minutes,
                    language=language,
                    characters_per_minute=characters_per_minute,
                )
                usage = generated.usage
                intelligence = {
                    row.event_id: row for row in generated.output.events
                }
                enriched = _merge_enrichment(events, intelligence)
                spoken_text = sanitize_spoken_text(
                    generated.output.spoken_text,
                    max_chars=round(target_minutes * characters_per_minute * 1.2),
                )
                if not _is_valid_chinese_script(
                    spoken_text,
                    language,
                    minimum_chars=round(
                        target_minutes * characters_per_minute * 0.8
                    ),
                ):
                    raise ValueError("LLM spoken script failed language or length validation")
                llm_succeeded = True
            except Exception as exc:
                spoken_text = ""
                fallback_reason = f"combined_editorial:{type(exc).__name__}"
            finally:
                editorial_latency_ms = (time.perf_counter() - started) * 1000
        elif events:
            fallback_reason = "llm_unavailable"

        if not spoken_text:
            spoken_text = deterministic_spoken_script(
                events,
                now=now,
                target_minutes=target_minutes,
                characters_per_minute=characters_per_minute,
            )

        briefing = Briefing(
            generated_at=now,
            event_count=len(events),
            estimated_duration_seconds=estimate_duration_seconds(
                spoken_text, characters_per_minute=characters_per_minute
            ),
            spoken_text=spoken_text,
            sections=_sections(enriched),
            sources=list(dict.fromkeys(item.source for event in events for item in event.items)),
            event_ids=[event.id for event in events],
            # Preserve the V1 API meaning: this flag denotes stale/cached service fallback.
            is_fallback=False,
        )
        return BriefingGenerationResult(
            briefing=briefing,
            editorial_calls=editorial_calls,
            editorial_latency_ms=editorial_latency_ms,
            usage=usage,
            llm_succeeded=llm_succeeded,
            fallback_used=bool(fallback_reason),
            fallback_reason=fallback_reason,
        )


def _fallback_enrichment(events: list[NewsEvent]) -> list[EnrichedNewsEvent]:
    return [
        EnrichedNewsEvent(event=event, intelligence=deterministic_event_intelligence(event))
        for event in events
    ]


def _merge_enrichment(
    events: list[NewsEvent], intelligence: dict[str, EventIntelligence]
) -> list[EnrichedNewsEvent]:
    result: list[EnrichedNewsEvent] = []
    for event in events:
        fallback = deterministic_event_intelligence(event)
        generated = intelligence.get(event.id)
        if generated is None:
            merged = fallback
        else:
            merged = EventIntelligence(
                event_id=event.id,
                summary_zh=generated.summary_zh or fallback.summary_zh,
                why_it_matters=generated.why_it_matters or fallback.why_it_matters,
                watch_next=generated.watch_next,
                confidence=generated.confidence,
                key_facts=generated.key_facts or fallback.key_facts,
                source_consensus=generated.source_consensus,
            )
        result.append(EnrichedNewsEvent(event=event, intelligence=merged))
    return result


def _sections(events: list[EnrichedNewsEvent]) -> list[BriefingSection]:
    grouped: dict[str, list[EnrichedNewsEvent]] = {}
    for enriched in events:
        category = enriched.event.categories[0] if enriched.event.categories else "world"
        grouped.setdefault(category, []).append(enriched)
    return [
        BriefingSection(
            name=category,
            event_ids=[enriched.event.id for enriched in group],
            text="；".join(enriched.intelligence.summary_zh for enriched in group),
        )
        for category, group in grouped.items()
    ]


def _is_valid_chinese_script(
    text: str, language: str, *, minimum_chars: int
) -> bool:
    if len(text) < minimum_chars:
        return False
    if not language.lower().startswith("zh"):
        return True
    chinese_characters = sum("\u3400" <= character <= "\u9fff" for character in text)
    return chinese_characters >= max(20, round(len(text) * 0.2))
