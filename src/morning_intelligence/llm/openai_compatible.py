from __future__ import annotations

import json
import re
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from morning_intelligence.domain import (
    EditorialBriefingOutput,
    EditorialBriefingResult,
    EventIntelligence,
    LLMUsage,
    NewsEvent,
)

from .base import LLMProvider
from .prompts import COMBINED_EDITORIAL_SYSTEM_PROMPT


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        thinking_mode: Literal["enabled", "disabled"] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.thinking_mode = thinking_mode
        self.transport = transport

    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult:
        target_chars = target_minutes * characters_per_minute
        minimum_chars = round(target_chars * 0.8)
        maximum_chars = round(target_chars * 1.2)
        user = json.dumps(
            {
                "language": language,
                "spoken_target": {
                    "target_characters": target_chars,
                    "minimum_characters": minimum_chars,
                    "maximum_characters": maximum_chars,
                },
                "selected_events": _event_payload(events),
                "output_schema": {
                    "events": [
                        {
                            "event_id": "selected event_id",
                            "summary_zh": "简体中文事实综合",
                            "why_it_matters": "与事实区分的克制影响解释",
                            "watch_next": "有证据基础的后续观察点",
                            "key_facts": ["最多六条输入证据支持的事实"],
                            "confidence": 0.0,
                            "source_consensus": "来源共识或分歧",
                        }
                    ],
                    "spoken_text": "完整的自然简体中文晨间口播纯文本",
                },
            },
            ensure_ascii=False,
        )
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": COMBINED_EDITORIAL_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        if self.thinking_mode is not None:
            request["thinking"] = {"type": self.thinking_mode}
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=request,
            )
            response.raise_for_status()
        body = response.json()
        text = str(body["choices"][0]["message"]["content"]).strip()
        output = parse_editorial_briefing(
            parse_json_object(text), expected_ids={event.id for event in events}
        )
        return EditorialBriefingResult(output=output, usage=parse_llm_usage(body))


def _event_payload(events: list[NewsEvent]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": event.id,
            "canonical_title": event.canonical_title,
            "first_seen_at": event.first_seen_at.isoformat(),
            "latest_seen_at": event.latest_seen_at.isoformat(),
            "categories": event.categories,
            "source_count": event.source_count,
            "deterministic_score": event.final_score,
            "global_importance": event.global_importance_score,
            "editorial_score": event.editorial_score,
            "evidence": _deduplicated_evidence(event),
        }
        for event in events
    ]


def _deduplicated_evidence(event: NewsEvent) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    descriptions: set[str] = set()
    for item in event.items:
        description = re.sub(r"\s+", " ", item.description).strip()[:800]
        normalized = description.casefold()
        if normalized and normalized in descriptions:
            description = ""
        if normalized:
            descriptions.add(normalized)
        evidence.append(
            {
                "source": item.source,
                "source_quality": item.source_quality,
                "title": item.title,
                "description": description,
                "published_at": item.published_at.isoformat(),
            }
        )
    return evidence


def parse_editorial_briefing(
    data: dict[str, Any], *, expected_ids: set[str]
) -> EditorialBriefingOutput:
    intelligence = parse_event_intelligence(data, expected_ids=expected_ids)
    spoken_text = data.get("spoken_text", "")
    return EditorialBriefingOutput(
        events=list(intelligence.values()),
        spoken_text=spoken_text if isinstance(spoken_text, str) else "",
    )


def parse_llm_usage(body: dict[str, Any]) -> LLMUsage:
    usage = body.get("usage", {})
    if not isinstance(usage, dict):
        return LLMUsage()
    return LLMUsage(
        prompt_tokens=_nonnegative_int(usage.get("prompt_tokens")),
        completion_tokens=_nonnegative_int(usage.get("completion_tokens")),
        total_tokens=_nonnegative_int(usage.get("total_tokens")),
        cache_hit_tokens=_nonnegative_int(usage.get("prompt_cache_hit_tokens")),
        cache_miss_tokens=_nonnegative_int(usage.get("prompt_cache_miss_tokens")),
    )


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_event_intelligence(
    data: dict[str, Any], *, expected_ids: set[str]
) -> dict[str, EventIntelligence]:
    """Validate rows independently so one malformed event cannot discard good rows."""
    rows = data.get("events", [])
    if not isinstance(rows, list):
        return {}
    result: dict[str, EventIntelligence] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        event_id = str(row.get("event_id", row.get("id", ""))).strip()
        if not event_id or event_id not in expected_ids or event_id in result:
            continue
        row["event_id"] = event_id
        row["key_facts"] = _clean_key_facts(row.get("key_facts", []))
        for field in ("summary_zh", "why_it_matters", "watch_next", "source_consensus"):
            row[field] = _clean_text(row.get(field, ""))
        try:
            result[event_id] = EventIntelligence.model_validate(row)
        except (ValidationError, TypeError, ValueError):
            continue
    return result


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    first, last = cleaned.find("{"), cleaned.rfind("}")
    if first < 0 or last < first:
        raise ValueError("LLM response did not contain a JSON object")
    value = json.loads(cleaned[first : last + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response was not a JSON object")
    return value


def _clean_key_facts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [cleaned for item in value[:6] if (cleaned := _clean_text(item))][:6]


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"https?://[^\s。！？；，,]+", " ", value)
    text = re.sub(r"[*_`#]+", "", text)
    return re.sub(r"\s+", " ", text).strip()
