from morning_intelligence.briefing import BriefingGenerator
from morning_intelligence.domain import (
    EnrichedNewsEvent,
    EventIntelligence,
    NewsEvent,
)
from morning_intelligence.llm import LLMProvider


class RecordingProvider(LLMProvider):
    def __init__(self) -> None:
        self.synthesis_calls = 0
        self.script_calls = 0

    async def score_events(self, events: list[NewsEvent]) -> dict[str, float]:
        raise AssertionError("V2 briefing generation must not add a third scoring call")

    async def synthesize_events(
        self, events: list[NewsEvent]
    ) -> dict[str, EventIntelligence]:
        self.synthesis_calls += 1
        return {
            event.id: EventIntelligence(
                event_id=event.id,
                summary_zh="美国多个州正在就青少年产品机制起诉 Meta。",
                why_it_matters="这可能影响社交平台面向未成年人的产品边界。",
                watch_next="法院是否要求 Meta 修改具体产品机制。",
                key_facts=["诉讼涉及青少年产品机制"],
                confidence=0.9,
            )
            for event in events
        }

    async def create_spoken_script(
        self,
        events: list[EnrichedNewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> str:
        self.script_calls += 1
        assert events[0].intelligence.summary_zh.startswith("美国多个州")
        assert events[0].intelligence.watch_next.startswith("法院")
        assert target_minutes == 5
        assert language == "zh-CN"
        assert characters_per_minute == 260
        return (
            "早上好，今天首先值得关注的是 Meta 面临的一场诉讼。"
            "美国多个州正在推动法院审视平台面向未成年人的产品机制。"
            "这件事可能影响未来的产品设计边界。接下来值得观察法院提出哪些具体要求。"
        )


class MalformedProvider(RecordingProvider):
    async def synthesize_events(
        self, events: list[NewsEvent]
    ) -> dict[str, EventIntelligence]:
        self.synthesis_calls += 1
        raise ValueError("malformed JSON")


async def test_generator_uses_two_stage_chinese_editorial_pipeline(make_item) -> None:
    event = _event(make_item)
    provider = RecordingProvider()

    result = await BriefingGenerator(provider).generate(
        [event], target_minutes=5, language="zh-CN", characters_per_minute=260
    )

    assert provider.synthesis_calls == 1
    assert provider.script_calls == 1
    assert result.synthesis_calls == 1
    assert result.script_calls == 1
    assert result.fallback_used is False
    assert "接下来值得观察" in result.briefing.spoken_text
    assert result.briefing.sections[0].text.startswith("美国多个州")


async def test_malformed_synthesis_falls_back_without_script_call(make_item) -> None:
    provider = MalformedProvider()

    result = await BriefingGenerator(provider).generate(
        [_event(make_item)], target_minutes=5, language="zh-CN"
    )

    assert provider.synthesis_calls == 1
    assert provider.script_calls == 0
    assert result.fallback_used is True
    assert result.fallback_reason == "event_synthesis:ValueError"
    assert result.briefing.spoken_text.startswith("早上好")


def _event(make_item) -> NewsEvent:
    item = make_item(
        "Meta faces a lawsuit from US states",
        "https://example.com/meta",
        category="technology",
        description="US states challenge product mechanisms aimed at young users.",
    )
    return NewsEvent(
        id="meta-case",
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
        categories=["technology"],
        source_count=2,
        global_importance_score=0.8,
        editorial_score=0.78,
    )
