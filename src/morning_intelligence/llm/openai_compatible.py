from __future__ import annotations

import json
import re
from typing import Any

import httpx

from morning_intelligence.domain import NewsEvent

from .base import LLMProvider
from .prompts import IMPORTANCE_SYSTEM, SPOKEN_SYSTEM, SUMMARY_SYSTEM


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

    async def summarize_events(self, events: list[NewsEvent]) -> dict[str, tuple[str, str]]:
        data = await self._json_completion(
            SUMMARY_SYSTEM,
            "Return {\"events\": [{\"id\":...,\"summary\":...,\"why_it_matters\":...}]}.\n"
            + json.dumps(_event_payload(events), ensure_ascii=False),
        )
        result: dict[str, tuple[str, str]] = {}
        for row in data.get("events", []):
            if isinstance(row, dict) and row.get("id"):
                result[str(row["id"])] = (
                    str(row.get("summary", "")),
                    str(row.get("why_it_matters", "")),
                )
        return result

    async def create_spoken_script(
        self, events: list[NewsEvent], *, target_minutes: int, language: str
    ) -> str:
        target_chars = max(500, min(1800, target_minutes * 240))
        user = (
            f"语言：{language}。目标约 {target_chars} 个中文字符，最长 1800 字。"
            "请自然报出日期、重点和结尾。证据如下：\n"
            + json.dumps(_event_payload(events), ensure_ascii=False)
        )
        return await self._text_completion(SPOKEN_SYSTEM, user)

    async def _json_completion(self, system: str, user: str) -> dict[str, Any]:
        text = await self._text_completion(system, user, response_format={"type": "json_object"})
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise ValueError("LLM response was not a JSON object")
        return value

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
