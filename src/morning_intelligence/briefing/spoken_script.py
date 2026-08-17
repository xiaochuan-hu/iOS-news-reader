from __future__ import annotations

import re
from datetime import datetime

from morning_intelligence.domain import NewsEvent


def deterministic_spoken_script(events: list[NewsEvent], *, now: datetime) -> str:
    date_text = f"{now.month}月{now.day}日"
    if not events:
        return f"早上好。今天是{date_text}。目前没有足够重要、并且经过验证的新消息。祝你今天顺利。"

    parts = [f"早上好。今天是{date_text}。今天最值得关注的事情有{len(events)}件。"]
    for index, event in enumerate(events, start=1):
        description = _best_description(event)
        transition = "首先" if index == 1 else f"第{_chinese_number(index)}件事"
        sentence = f"{transition}，{event.canonical_title}。"
        if description and description.casefold() not in event.canonical_title.casefold():
            sentence += f"{description}。"
        if event.source_count >= 2:
            sentence += f"这件事得到{_chinese_number(event.source_count)}个来源的交叉确认。"
        if event.why_it_matters:
            sentence += f"它的影响是，{event.why_it_matters.rstrip('。')}。"
        parts.append(sentence)
    parts.append("以上是今天的晨间新闻。接下来值得继续观察这些事件是否出现新的可靠进展。")
    return sanitize_spoken_text("".join(parts))


def sanitize_spoken_text(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\[(\d+)\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1800].rstrip("，,；; ")


def estimate_duration_seconds(text: str) -> int:
    speakable = re.sub(r"\s+", "", text)
    return max(1, round(len(speakable) / 4.0))


def _best_description(event: NewsEvent) -> str:
    if event.summary:
        return event.summary.strip().rstrip("。")
    description = max((item.description for item in event.items), key=len, default="")
    cleaned = re.sub(r"\s+", " ", description).strip()
    return cleaned[:180].rstrip("。.!！")


def _chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value == 10:
        return "十"
    if value < 20:
        return "十" + digits[value - 10]
    return str(value)
