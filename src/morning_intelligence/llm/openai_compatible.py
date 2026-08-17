from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from morning_intelligence.domain import EnrichedNewsEvent, EventIntelligence, NewsEvent

from .base import LLMProvider
from .prompts import EVENT_SYNTHESIS_SYSTEM_PROMPT, IMPORTANCE_SYSTEM, SPOKEN_SYSTEM


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def score_events(self, events: list[NewsEvent]) -> dict[str, float]:
        payload = _event_payload(events)
        data = await self._json_completion(
            IMPORTANCE_SYSTEM,
            "Score each event from 0 to 1. Return {\"scores\": {\"event_id\": number}}.\n"
            + json.dumps(payload, ensure_ascii=False),
        )
        raw_scores = data.get("scores", {})
        return {
            str(event_id): min(1.0, max(0.0, float(score)))
            for event_id, score in raw_scores.items()
        }

    async def synthesize_events(
        self, events: list[NewsEvent]
    ) -> dict[str, EventIntelligence]:
        data = await self._json_completion(
            EVENT_SYNTHESIS_SYSTEM_PROMPT,
            "输入和输出约定如下。只返回 output_schema 对应的 JSON。\n"
            + json.dumps(
                {
                    "events": _event_payload(events),
                    "output_schema": {
                        "events": [
                            {
                                "event_id": "输入中的 event id",
                                "summary_zh": "简体中文事实综合",
                                "why_it_matters": "与事实区分的克制影响解释",
                                "watch_next": "有证据基础的后续观察点",
                                "key_facts": ["最多六条事实"],
                                "confidence": 0.0,
                                "source_consensus": "来源一致性或分歧",
                            }
                        ]
                    },
                },
                ensure_ascii=False,
            ),
        )
        return parse_event_intelligence(data, expected_ids={event.id for event in events})

    async def create_spoken_script(
        self,
        events: list[EnrichedNewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> str:
        target_chars = target_minutes * characters_per_minute
        minimum_chars = round(target_chars * 0.8)
        maximum_chars = round(target_chars * 1.2)
        user = (
            f"语言：{language}。目标约 {target_chars} 个中文字符，建议范围 "
            f"{minimum_chars}–{maximum_chars} 字。请自然报出开场、重点、主题转场和结尾。"
            "以下内容已经过事实编辑；不得恢复原始文章列表，也不得补充外部事实：\n"
            + json.dumps(_spoken_payload(events), ensure_ascii=False)
        )
        return await self._text_completion(SPOKEN_SYSTEM, user)

    async def _json_completion(self, system: str, user: str) -> dict[str, Any]:
        text = await self._text_completion(system, user, response_format={"type": "json_object"})
        return parse_json_object(text)

    async def _text_completion(
        self,
        system: str,
        user: str,
        *,
        response_format: dict[str, str] | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if response_format:
            request["response_format"] = response_format
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
        return str(body["choices"][0]["message"]["content"]).strip()


def _event_payload(events: list[NewsEvent]) -> list[dict[str, Any]]:
    return [
        {
            "id": event.id,
            "title": event.canonical_title,
            "published_at": event.latest_seen_at.isoformat(),
            "categories": event.categories,
            "source_count": event.source_count,
            "deterministic_score": event.final_score,
            "evidence": [
                {
                    "source": item.source,
                    "title": item.title,
                    "description": item.description[:800],
                    "published_at": item.published_at.isoformat(),
                }
                for item in event.items
            ],
        }
        for event in events
    ]


def _spoken_payload(events: list[EnrichedNewsEvent]) -> list[dict[str, Any]]:
    return [
        {
            "canonical_title": enriched.event.canonical_title,
            "categories": enriched.event.categories,
            "global_importance": enriched.event.global_importance_score,
            "editorial_score": enriched.event.editorial_score,
            **enriched.intelligence.model_dump(exclude={"event_id"}),
        }
        for enriched in events
    ]


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
