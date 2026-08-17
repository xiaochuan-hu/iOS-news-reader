from datetime import UTC, datetime

from morning_intelligence.briefing.spoken_script import (
    deterministic_event_intelligence,
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


def test_sanitizer_removes_html_encoded_url() -> None:
    assert sanitize_spoken_text("更新 https:&#x2F;&#x2F;example.com&#x2F;story 后续") == (
        "更新 后续"
    )


def test_url_removal_does_not_consume_following_chinese_sentence() -> None:
    cleaned = sanitize_spoken_text("链接 https://example.com/story。下一条新闻继续。")
    assert "https" not in cleaned
    assert "下一条新闻继续" in cleaned


def test_url_only_description_falls_back_to_event_title(make_item) -> None:
    item = make_item(
        "AI regulation update",
        "https://example.com/story",
        description="https://social.example/post/123",
    )
    event = NewsEvent(
        canonical_title=item.title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
    )
    assert deterministic_event_intelligence(event).summary_zh == "AI regulation update"


def test_target_minutes_changes_deterministic_detail_level(make_item) -> None:
    events = []
    for index, category in enumerate(("world", "technology", "china", "finance")):
        item = make_item(
            f"事件{index}",
            f"https://example.com/{index}",
            category=category,
            description=f"这是第{index}件事件中已经得到报道的具体事实",
        )
        events.append(
            NewsEvent(
                canonical_title=item.title,
                items=[item],
                first_seen_at=item.published_at,
                latest_seen_at=item.published_at,
                categories=[category],
                source_count=2,
                why_it_matters="它可能影响相关行业接下来的决策",
            )
        )

    short = deterministic_spoken_script(
        events, now=datetime(2026, 8, 17, tzinfo=UTC), target_minutes=1
    )
    long = deterministic_spoken_script(
        events, now=datetime(2026, 8, 17, tzinfo=UTC), target_minutes=5
    )

    assert len(long) > len(short)
    assert "值得关注，因为" not in short
    assert "值得关注，因为" in long
