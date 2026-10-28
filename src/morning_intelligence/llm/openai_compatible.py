from __future__ import annotations

import json
import re
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from morning_intelligence.domain import (
    EditorialBriefingOutput,
    EditorialBriefingResult,
    EmailMessage,
    EventIntelligence,
    GitHubRepositoryIntelligence,
    GitHubTrendingRepo,
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
        github_repositories: list[GitHubTrendingRepo] | None = None,
        email_messages: list[EmailMessage] | None = None,
    ) -> EditorialBriefingResult:
        # V5 intentionally accepts these legacy arguments without turning them into
        # a completion budget. Deep analysis determines the appropriate length.
        del target_minutes, characters_per_minute
        user = json.dumps(
            {
                "language": language,
                "briefing_mode": "v5.1_deep_briefing_quality_upgrade",
                "soft_length_guidance": {
                    "whole_briefing_minutes": "10-15",
                    "ordinary_event_chinese_characters": "200-300",
                    "major_event_chinese_characters": "300-500",
                    "hard_limit": False,
                },
                "fresh_news_events": _event_payload(events),
                "ucl_email_messages": _email_payload(email_messages or []),
                "github_trending_top10": [
                    repository.model_dump() for repository in (github_repositories or [])
                ],
                "output_schema": {
                    "events": [
                        {
                            "event_id": "selected event_id",
                            "summary_zh": "Event：发生了什么",
                            "background_zh": "Background：技术、商业或政策背景",
                            "why_it_matters": "Impact：为什么重要",
                            "watch_next": "What to watch：未来观察点",
                            "key_facts": ["最多六条输入证据支持的事实"],
                            "confidence": 0.0,
                            "source_consensus": "来源共识或分歧",
                        }
                    ],
                    "email_spoken_text": (
                        "只含个人晨间收件箱摘要的自然简体中文口播纯文本；无邮件时为空"
                    ),
                    "news_spoken_text": "只含新闻的自然简体中文口播纯文本",
                    "github_repositories": [
                        {
                            "repo": "输入中的 owner/name",
                            "summary_zh": "What it does：一句用途概括",
                            "why_trending": "Why trending：一句上榜原因",
                            "why_it_matters": "Developer significance：一句开发者意义",
                            "main_technology": "Main technology：主要语言或技术",
                        }
                    ],
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
            parse_json_object(text),
            expected_ids={event.id for event in events},
            expected_repositories={
                repository.full_name for repository in (github_repositories or [])
            },
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
            "novelty": event.novelty_score,
            "technical_importance": event.llm_importance_score,
            "source_authority": event.source_score,
            "editorial_score": event.editorial_score,
            "evidence": _deduplicated_evidence(event),
        }
        for event in events
    ]


def _email_payload(messages: list[EmailMessage]) -> list[dict[str, Any]]:
    return [
        {
            "message_id": message.message_id,
            "received_at": message.received_at.isoformat(),
            "sender": message.sender,
            "subject": message.subject,
            "snippet": message.snippet,
            "body_text": message.body_text[:4000],
        }
        for message in messages
        if not message.subject.startswith("UCL Morning Brief |")
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
                "published_at": item.published_at.isoformat() if item.published_at else None,
            }
        )
    return evidence


def parse_editorial_briefing(
    data: dict[str, Any], *, expected_ids: set[str], expected_repositories: set[str] | None = None
) -> EditorialBriefingOutput:
    intelligence = parse_event_intelligence(data, expected_ids=expected_ids)
    spoken_text = data.get("news_spoken_text", data.get("spoken_text", ""))
    email_spoken_text = data.get("email_spoken_text", "")
    return EditorialBriefingOutput(
        events=list(intelligence.values()),
        email_spoken_text=(
            email_spoken_text if isinstance(email_spoken_text, str) else ""
        ),
        news_spoken_text=spoken_text if isinstance(spoken_text, str) else "",
        github_repositories=_parse_github_intelligence(
            data, expected_repositories=expected_repositories or set()
        ),
    )


def _parse_github_intelligence(
    data: dict[str, Any], *, expected_repositories: set[str]
) -> list[GitHubRepositoryIntelligence]:
    rows = data.get("github_repositories", [])
    if not isinstance(rows, list):
        return []
    expected = {repo.casefold(): repo for repo in expected_repositories}
    seen: set[str] = set()
    result: list[GitHubRepositoryIntelligence] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("repo", "")).strip().casefold()
        if key not in expected or key in seen:
            continue
        try:
            result.append(
                GitHubRepositoryIntelligence(
                    repo=expected[key],
                    summary_zh=_clean_text(raw.get("summary_zh", "")),
                    why_it_matters=_clean_text(raw.get("why_it_matters", "")),
                    why_trending=_clean_text(raw.get("why_trending", "")),
                    main_technology=_clean_text(raw.get("main_technology", "")),
                )
            )
            seen.add(key)
        except (ValidationError, TypeError, ValueError):
            continue
    return result


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
        for field in (
            "summary_zh",
            "background_zh",
            "why_it_matters",
            "watch_next",
            "source_consensus",
        ):
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
