from datetime import UTC, datetime

from morning_intelligence.briefing.spoken_script import (
    deterministic_spoken_script,
    sanitize_spoken_text,
)
from morning_intelligence.domain import NewsEvent


def test_spoken_script_is_plain_text_and_does_not_read_urls(make_item) -> None:
    item = make_item(
        "一家人工智能公司发布新模型",
        "https://example.com/model",
        description="新模型面向软件开发者开放，并公布了测试结果",
    )
    event = NewsEvent(
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
        source_count=2,
    )
    script = deterministic_spoken_script(
        [event], now=datetime(2026, 8, 17, tzinfo=UTC)
    )
    assert script.startswith("早上好。今天是8月17日")
    assert "http" not in script
    assert "#" not in script
    assert "*" not in script


def test_no_news_does_not_invent_filler() -> None:
    script = deterministic_spoken_script([], now=datetime(2026, 8, 17, tzinfo=UTC))
    assert "没有足够重要" in script


def test_sanitizer_removes_markdown_and_citations() -> None:
    assert sanitize_spoken_text("## 头条\n- **内容** [1] https://example.com") == "头条 内容"
