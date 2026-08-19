from __future__ import annotations

import json

import httpx
import pytest

from morning_intelligence.domain import GitHubTrendingRepo, NewsEvent
from morning_intelligence.llm import OpenAICompatibleProvider, parse_editorial_briefing
from morning_intelligence.llm.prompts import COMBINED_EDITORIAL_SYSTEM_PROMPT


def _event(make_item, event_id: str = "event-1") -> NewsEvent:
    first = make_item(
        "Meta faces a lawsuit from US states",
        "https://example.com/meta",
        source="Source A",
        description="US states are challenging youth product design practices.",
    )
    second = make_item(
        "States challenge Meta youth product design",
        "https://example.org/meta",
        source="Source B",
        description="US states are challenging youth product design practices.",
    )
    return NewsEvent(
        id=event_id,
        canonical_title=first.title,
        items=[first, second],
        first_seen_at=first.published_at,
        latest_seen_at=second.published_at,
        categories=["technology"],
        source_count=2,
        final_score=0.8,
        global_importance_score=0.85,
        editorial_score=0.82,
    )


def _long_chinese_script() -> str:
    sentence = (
        "今天值得关注的进展已经得到多个来源报道，"
        "接下来需要观察公开信息是否出现新的可靠变化。"
    )
    return "早上好。" + sentence * 28


async def test_combined_provider_makes_exactly_one_grounded_json_request(make_item) -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        content = {
            "events": [
                {
                    "event_id": "event-1",
                    "summary_zh": "美国多个州正就青少年产品机制起诉 Meta。",
                    "why_it_matters": "这可能影响平台面向未成年人的产品边界。",
                    "watch_next": "法院是否提出具体要求。",
                    "key_facts": ["诉讼涉及青少年产品机制"],
                    "confidence": 0.9,
                    "source_consensus": "两个来源的核心表述一致",
                },
                {"event_id": "unknown", "summary_zh": "不得进入结果"},
                {"event_id": "event-1", "summary_zh": "重复行不得覆盖"},
            ],
            "spoken_text": _long_chinese_script(),
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {
                    "prompt_tokens": 101,
                    "completion_tokens": 202,
                    "total_tokens": 303,
                    "prompt_cache_hit_tokens": 41,
                    "prompt_cache_miss_tokens": 60,
                },
            },
            request=request,
        )

    result = await _provider(handler, thinking_mode="disabled").generate_editorial_briefing(
        [_event(make_item)],
        target_minutes=5,
        language="zh-CN",
        characters_per_minute=260,
    )

    assert len(calls) == 1
    request = calls[0]
    assert request["temperature"] == 0.2
    assert request["response_format"] == {"type": "json_object"}
    assert request["thinking"] == {"type": "disabled"}
    assert "事实边界" in request["messages"][0]["content"]  # type: ignore[index]
    user_payload = json.loads(request["messages"][1]["content"])  # type: ignore[index]
    selected = user_payload["fresh_news_events"]
    assert selected[0]["event_id"] == "event-1"
    assert selected[0]["global_importance"] == 0.85
    assert len(selected[0]["evidence"]) == 2
    assert selected[0]["evidence"][1]["description"] == ""
    assert [row.event_id for row in result.output.events] == ["event-1"]
    assert result.output.spoken_text.startswith("早上好")
    assert result.usage.total_tokens == 303
    assert result.usage.cache_hit_tokens == 41


def test_combined_parser_isolates_bad_unknown_and_duplicate_rows() -> None:
    output = parse_editorial_briefing(
        {
            "events": [
                {"event_id": "first", "summary_zh": "坏置信度", "confidence": 2},
                {"event_id": "second", "summary_zh": "第二件事", "confidence": 0.8},
                {"event_id": "unknown", "summary_zh": "未知"},
                {"event_id": "second", "summary_zh": "重复"},
                "not-an-object",
            ],
            "spoken_text": "有效文本",
        },
        expected_ids={"first", "second"},
    )
    assert [row.event_id for row in output.events] == ["second"]
    assert output.events[0].summary_zh == "第二件事"


def test_combined_parser_accepts_only_first_valid_input_github_repository() -> None:
    output = parse_editorial_briefing(
        {
            "events": [],
            "news_spoken_text": "新闻正文",
            "github_repositories": [
                {"repo": "owner/known", "summary_zh": "首个有效概括"},
                {"repo": "OWNER/KNOWN", "summary_zh": "重复不得覆盖"},
                {"repo": "owner/unknown", "summary_zh": "未知不得进入"},
            ],
        },
        expected_ids=set(),
        expected_repositories={"owner/known"},
    )

    assert len(output.github_repositories) == 1
    assert output.github_repositories[0].repo == "owner/known"
    assert output.github_repositories[0].summary_zh == "首个有效概括"


async def test_combined_provider_sends_github_in_same_single_request(make_item) -> None:
    calls: list[dict[str, object]] = []
    repository = GitHubTrendingRepo(
        rank=1,
        full_name="owner/repo",
        url="https://github.com/owner/repo",
        description="Only supplied repository description",
        language="Python",
        stars_today=123,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        content = {
            "events": [],
            "news_spoken_text": _long_chinese_script(),
            "github_repositories": [
                {
                    "repo": "owner/repo",
                    "summary_zh": "只依据输入的项目描述",
                    "why_it_matters": "今天的关注度值得观察",
                }
            ],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
            request=request,
        )

    result = await _provider(handler).generate_editorial_briefing(
        [_event(make_item)],
        target_minutes=9,
        language="zh-CN",
        characters_per_minute=260,
        github_repositories=[repository],
    )

    assert len(calls) == 1
    payload = json.loads(calls[0]["messages"][1]["content"])  # type: ignore[index]
    assert payload["github_trending_top10"][0]["full_name"] == "owner/repo"
    assert result.output.github_repositories[0].repo == "owner/repo"


@pytest.mark.parametrize("thinking_mode", [None, "disabled", "enabled"])
async def test_optional_thinking_payload(thinking_mode: str | None, make_item) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if thinking_mode is None:
            assert "thinking" not in body
        else:
            assert body["thinking"] == {"type": thinking_mode}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"events": [], "spoken_text": _long_chinese_script()}
                            )
                        }
                    }
                ]
            },
            request=request,
        )

    await _provider(handler, thinking_mode=thinking_mode).generate_editorial_briefing(
        [_event(make_item)], target_minutes=5, language="zh-CN", characters_per_minute=260
    )
    assert calls == 1


@pytest.mark.parametrize("status_code", [400, 500])
async def test_http_errors_are_never_retried(status_code: int, make_item) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, request=request)

    with pytest.raises(httpx.HTTPStatusError):
        await _provider(handler).generate_editorial_briefing(
            [_event(make_item)], target_minutes=5, language="zh-CN", characters_per_minute=260
        )
    assert calls == 1


@pytest.mark.parametrize("content", ["not json", "", "{}"])
async def test_invalid_combined_responses_are_never_retried(
    content: str, make_item
) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
            request=request,
        )

    provider = _provider(handler)
    if content == "{}":
        result = await provider.generate_editorial_briefing(
            [_event(make_item)], target_minutes=5, language="zh-CN", characters_per_minute=260
        )
        assert result.output.spoken_text == ""
    else:
        with pytest.raises((ValueError, json.JSONDecodeError)):
            await provider.generate_editorial_briefing(
                [_event(make_item)],
                target_minutes=5,
                language="zh-CN",
                characters_per_minute=260,
            )
    assert calls == 1


async def test_timeout_is_never_retried(make_item) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(httpx.ReadTimeout):
        await _provider(handler).generate_editorial_briefing(
            [_event(make_item)], target_minutes=5, language="zh-CN", characters_per_minute=260
        )
    assert calls == 1


def test_combined_prompt_enforces_grounding_and_natural_spoken_output() -> None:
    for phrase in (
        "严禁利用模型记忆",
        "来源有分歧",
        "why_it_matters",
        "global_importance",
        "约九分钟",
        "不要机械说第一条",
        "不得输出网址",
    ):
        assert phrase in COMBINED_EDITORIAL_SYSTEM_PROMPT


def _provider(
    handler, *, thinking_mode: str | None = None
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key="secret",
        base_url="https://llm.example/v1",
        model="test",
        timeout_seconds=2,
        thinking_mode=thinking_mode,  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )
