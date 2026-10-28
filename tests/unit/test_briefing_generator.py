from __future__ import annotations

import pytest

from morning_intelligence.briefing import BriefingGenerator
from morning_intelligence.domain import (
    EditorialBriefingOutput,
    EditorialBriefingResult,
    EmailMessage,
    EventIntelligence,
    GitHubTrendingRepo,
    LLMUsage,
    NewsEvent,
)
from morning_intelligence.llm import LLMProvider


def _long_script() -> str:
    sentence = (
        "这项进展已经由多个来源报道，它可能影响相关参与者的下一步选择，"
        "后续仍需观察可靠信息。"
    )
    return "早上好。" + sentence * 30


class RecordingProvider(LLMProvider):
    def __init__(self, *, spoken_text: str | None = None) -> None:
        self.calls = 0
        self.spoken_text = spoken_text if spoken_text is not None else _long_script()

    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult:
        self.calls += 1
        assert target_minutes == 5
        assert language == "zh-CN"
        assert characters_per_minute == 260
        return EditorialBriefingResult(
            output=EditorialBriefingOutput(
                events=[
                    EventIntelligence(
                        event_id=events[0].id,
                        summary_zh="美国多个州正在就青少年产品机制起诉 Meta。",
                        why_it_matters="这可能影响社交平台面向未成年人的产品边界。",
                        watch_next="法院是否要求 Meta 修改具体产品机制。",
                        key_facts=["诉讼涉及青少年产品机制"],
                        confidence=0.9,
                    )
                ],
                spoken_text=self.spoken_text,
            ),
            usage=LLMUsage(
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
            ),
        )


class FailingProvider(RecordingProvider):
    async def generate_editorial_briefing(
        self,
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult:
        self.calls += 1
        raise ValueError("malformed JSON")


class EmailRecordingProvider(RecordingProvider):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.email_messages: list[EmailMessage] = []

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
        del target_minutes, language, characters_per_minute, github_repositories
        self.calls += 1
        self.email_messages = email_messages or []
        if self.fail:
            raise ValueError("malformed JSON")
        return EditorialBriefingResult(
            output=EditorialBriefingOutput(
                events=[],
                email_spoken_text="邮件简报：课程教室有变化。今日行动：查看 Moodle 通知。",
                news_spoken_text=_long_script(),
            )
        )


async def test_generator_uses_one_combined_editorial_call(make_item) -> None:
    event = _event(make_item)
    provider = RecordingProvider()

    result = await BriefingGenerator(provider).generate(
        [event], target_minutes=5, language="zh-CN", characters_per_minute=260
    )

    assert provider.calls == 1
    assert result.editorial_calls == 1
    assert result.llm_succeeded is True
    assert result.fallback_used is False
    assert result.usage.total_tokens == 300
    assert result.briefing.spoken_text.startswith("早上好")
    assert result.briefing.spoken_text.count("早上好") == 1
    assert result.briefing.sections[0].text.startswith("美国多个州")


async def test_missing_event_enrichment_uses_deterministic_row_fallback(make_item) -> None:
    event = _event(make_item)
    provider = RecordingProvider()

    async def partial(
        events: list[NewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> EditorialBriefingResult:
        provider.calls += 1
        return EditorialBriefingResult(
            output=EditorialBriefingOutput(events=[], spoken_text=_long_script())
        )

    provider.generate_editorial_briefing = partial  # type: ignore[method-assign]
    result = await BriefingGenerator(provider).generate(
        [event], target_minutes=5, language="zh-CN"
    )

    assert provider.calls == 1
    assert result.fallback_used is False
    assert "US states challenge" in result.briefing.sections[0].text


async def test_provider_failure_falls_back_after_one_call(make_item) -> None:
    provider = FailingProvider()

    result = await BriefingGenerator(provider).generate(
        [_event(make_item)], target_minutes=5, language="zh-CN"
    )

    assert provider.calls == 1
    assert result.editorial_calls == 1
    assert result.llm_succeeded is False
    assert result.fallback_used is True
    assert result.fallback_reason == "combined_editorial:ValueError"
    assert result.briefing.spoken_text.startswith("早上好")


@pytest.mark.parametrize(
    "spoken_text",
    [
        "太短",
        "This is a long English script. " * 80,
        '{"spoken_text":"这是残留的 JSON 片段"}',
    ],
)
async def test_invalid_spoken_text_uses_local_fallback_without_repair_call(
    spoken_text: str, make_item
) -> None:
    provider = RecordingProvider(spoken_text=spoken_text)

    result = await BriefingGenerator(provider).generate(
        [_event(make_item)], target_minutes=5, language="zh-CN"
    )

    assert provider.calls == 1
    assert result.fallback_used is True
    assert result.briefing.spoken_text.startswith("早上好")


async def test_spoken_text_is_sanitized_without_another_call(make_item) -> None:
    decorated = "# 标题\n" + _long_script() + " https://example.com 【1】 **结束**"
    provider = RecordingProvider(spoken_text=decorated)

    result = await BriefingGenerator(provider).generate(
        [_event(make_item)], target_minutes=5, language="zh-CN"
    )

    assert provider.calls == 1
    assert result.fallback_used is False
    assert "http" not in result.briefing.spoken_text
    assert "【1】" not in result.briefing.spoken_text
    assert "*" not in result.briefing.spoken_text


async def test_final_spoken_order_is_date_email_news_then_github(make_item) -> None:
    provider = EmailRecordingProvider()
    email = _email(body_text="Room change evidence")
    repository = GitHubTrendingRepo(
        rank=1,
        full_name="owner/repo",
        url="https://github.com/owner/repo",
        description="A developer tool",
    )

    result = await BriefingGenerator(provider).generate(
        [_event(make_item)],
        target_minutes=5,
        language="zh-CN",
        local_date=email.received_at.date(),
        email_messages=[email],
        github_repositories=[repository],
    )

    spoken = result.briefing.spoken_text
    assert provider.calls == 1
    assert provider.email_messages == [email]
    assert spoken.index("今天是2026年10月28日") < spoken.index("邮件简报")
    assert spoken.index("邮件简报") < spoken.index("这项进展已经由多个来源报道")
    assert spoken.index("这项进展已经由多个来源报道") < spoken.index("GitHub 今日热榜")


async def test_email_llm_failure_never_reads_raw_email_body(make_item) -> None:
    provider = EmailRecordingProvider(fail=True)
    raw_body = "PRIVATE RAW EMAIL BODY MUST NOT BE SPOKEN"

    result = await BriefingGenerator(provider).generate(
        [_event(make_item)],
        target_minutes=5,
        language="zh-CN",
        email_messages=[_email(body_text=raw_body)],
    )

    assert provider.calls == 1
    assert result.fallback_used is True
    assert raw_body not in result.briefing.spoken_text
    assert "邮件简报" not in result.briefing.spoken_text


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


def _email(*, body_text: str) -> EmailMessage:
    return EmailMessage(
        message_id="gmail-1",
        received_at="2026-10-28T05:04:00Z",
        sender="UCL <ucl@example.test>",
        recipients=["student@example.test"],
        subject="Room change",
        snippet="The room changed",
        body_text=body_text,
    )
