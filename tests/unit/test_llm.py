import json

import httpx

from morning_intelligence.domain import NewsEvent
from morning_intelligence.llm import OpenAICompatibleProvider


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
