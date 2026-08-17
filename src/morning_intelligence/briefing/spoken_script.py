from __future__ import annotations

import html
import re
from datetime import datetime

from morning_intelligence.domain import EventIntelligence, NewsEvent

CATEGORY_TRANSITIONS = {
    "ai_llm_agent": "AI 和科技方面，",
    "technology": "科技方面，",
    "world": "再看国际新闻，",
    "uk": "英国方面，",
    "china": "中国方面，",
    "finance": "商业和市场方面，",
}


def deterministic_spoken_script(
    events: list[NewsEvent],
    *,
    now: datetime,
    target_minutes: int = 5,
    characters_per_minute: int = 260,
) -> str:
    date_text = f"{now.month}月{now.day}日"
    if not events:
        return f"早上好。今天是{date_text}。目前没有足够重要、并且经过验证的新消息。祝你今天顺利。"

    target_chars = target_minutes * characters_per_minute
    detail_budget = max(90, target_chars // max(1, len(events)))
    parts = [f"早上好。今天是{date_text}。今天有{len(events)}件事情值得关注。"]
    previous_category = ""
    for index, event in enumerate(events):
        intelligence = deterministic_event_intelligence(event)
        category = event.categories[0] if event.categories else "world"
        if index == 0:
            transition = "今天首先值得关注的是，"
        elif category != previous_category:
            transition = CATEGORY_TRANSITIONS.get(category, "另外，")
        else:
            transition = "同一主题下，"
        sentence = f"{transition}{intelligence.summary_zh.rstrip('。')}。"
        if detail_budget >= 150 and intelligence.why_it_matters:
            sentence += f"这件事值得关注，因为{intelligence.why_it_matters.rstrip('。')}。"
        if detail_budget >= 210 and event.source_count >= 2:
            sentence += "目前有多个独立来源报道了这一进展。"
        if detail_budget >= 250 and intelligence.watch_next:
            sentence += f"接下来值得观察的是，{intelligence.watch_next.rstrip('。')}。"
        parts.append(sentence)
        previous_category = category
    parts.append("最后，今天值得继续观察的是这些事件是否出现新的可靠进展。祝你今天顺利。")
    return sanitize_spoken_text("".join(parts), max_chars=round(target_chars * 1.2))


def deterministic_event_intelligence(event: NewsEvent) -> EventIntelligence:
    description = _best_description(event)
    summary = description or event.canonical_title
    return EventIntelligence(
        event_id=event.id,
        summary_zh=summary,
        why_it_matters=event.why_it_matters,
        watch_next="",
        key_facts=[summary] if summary else [],
        confidence=None,
        source_consensus=(
            f"{event.source_count}个来源报道" if event.source_count >= 2 else "单一来源"
        ),
    )


def sanitize_spoken_text(text: str, *, max_chars: int = 1800) -> str:
    text = html.unescape(text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"https?://[^\s。！？；，,]+", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\[(\d+)\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text.rstrip("，,；; ")
    clipped = text[:max_chars]
    sentence_end = max(clipped.rfind(symbol) for symbol in "。！？")
    if sentence_end >= round(max_chars * 0.75):
        clipped = clipped[: sentence_end + 1]
    return clipped.rstrip("，,；; ")


def estimate_duration_seconds(text: str, *, characters_per_minute: int = 260) -> int:
    speakable = re.sub(r"\s+", "", text)
    return max(1, round(len(speakable) / characters_per_minute * 60))


def _best_description(event: NewsEvent) -> str:
    if event.summary:
        return event.summary.strip().rstrip("。")
    description = max((item.description for item in event.items), key=len, default="")
    cleaned = re.sub(r"\s+", " ", description).strip()
    if cleaned.casefold().startswith(("http://", "https://")):
        return ""
    return cleaned[:260].rstrip("。.!！")
