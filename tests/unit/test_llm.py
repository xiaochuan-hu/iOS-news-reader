import json

import httpx
import pytest

from morning_intelligence.domain import NewsEvent
from morning_intelligence.llm import OpenAICompatibleProvider
from morning_intelligence.llm.prompts import EVENT_SYNTHESIS_SYSTEM_PROMPT


async def test_openai_compatible_provider_batches_event_scores(make_item) -> None:
    item = make_item("Major event", "https://example.com/event")
    event = NewsEvent(
        id="event-1",
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert len(body["messages"]) == 2
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"scores":{"event-1":0.9}}'}}]},
            request=request,
        )

    provider = OpenAICompatibleProvider(
        api_key="secret",
        base_url="https://llm.example/v1",
        model="test",
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    assert await provider.score_events([event]) == {"event-1": 0.9}


async def test_structured_synthesis_accepts_partial_rows_and_rejects_unknown_ids(
    make_item,
) -> None:
    item = make_item(
        "Meta faces a lawsuit from US states",
        "https://example.com/meta",
        description="US states are challenging youth product design practices.",
    )
    event = NewsEvent(
        id="event-1",
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "简体中文" in body["messages"][0]["content"]
        assert "Meta faces a lawsuit" in body["messages"][1]["content"]
        content = {
            "events": [
                {
                    "event_id": "event-1",
                    "summary_zh": "美国多个州正就青少年产品机制起诉 Meta。",
                    "key_facts": ["诉讼针对青少年产品机制"],
                    "confidence": 0.9,
                },
                {"event_id": "unknown", "summary_zh": "不得进入结果"},
                {"event_id": "event-1", "summary_zh": "重复行不得覆盖"},
            ]
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
            request=request,
        )

    provider = _provider(handler)
    result = await provider.synthesize_events([event])

    assert list(result) == ["event-1"]
    assert result["event-1"].summary_zh.startswith("美国多个州")
    assert result["event-1"].watch_next == ""


async def test_structured_synthesis_isolates_malformed_event_rows(make_item) -> None:
    first_item = make_item("First", "https://example.com/first")
    second_item = make_item("Second", "https://example.com/second")
    events = [
        NewsEvent(
            id=event_id,
            canonical_title=item.title,
            items=[item],
            first_seen_at=item.published_at,
            latest_seen_at=item.published_at,
        )
        for event_id, item in (("first", first_item), ("second", second_item))
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        content = {
            "events": [
                {"event_id": "first", "summary_zh": "第一件事", "confidence": 2},
                {"event_id": "second", "summary_zh": "第二件事", "confidence": 0.8},
            ]
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
            request=request,
        )

    assert list(await _provider(handler).synthesize_events(events)) == ["second"]


async def test_malformed_json_raises_provider_error(make_item) -> None:
    item = make_item("Event", "https://example.com/event")
    event = NewsEvent(
        id="event",
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not json"}}]},
            request=request,
        )

    with pytest.raises(ValueError, match="JSON object"):
        await _provider(handler).synthesize_events([event])


@pytest.mark.parametrize("thinking_mode", [None, "disabled", "enabled"])
async def test_optional_thinking_payload(thinking_mode: str | None, make_item) -> None:
    item = make_item("Major event", "https://example.com/event")
    event = NewsEvent(
        id="event-1",
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if thinking_mode is None:
            assert "thinking" not in body
        else:
            assert body["thinking"] == {"type": thinking_mode}
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"events":[]}'}}]},
            request=request,
        )

    provider = OpenAICompatibleProvider(
        api_key="secret",
        base_url="https://llm.example/v1",
        model="test",
        timeout_seconds=2,
        thinking_mode=thinking_mode,  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )

    assert await provider.synthesize_events([event]) == {}


def test_event_synthesis_prompt_separates_fact_from_interpretation() -> None:
    assert "不得发明统计数字" in EVENT_SYNTHESIS_SYSTEM_PROMPT
    assert "why_it_matters" in EVENT_SYNTHESIS_SYSTEM_PROMPT
    assert "与已发生事实明确区分" in EVENT_SYNTHESIS_SYSTEM_PROMPT


def _provider(handler) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key="secret",
        base_url="https://llm.example/v1",
        model="test",
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
