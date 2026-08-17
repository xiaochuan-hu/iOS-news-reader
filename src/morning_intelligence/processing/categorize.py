from collections.abc import Iterable

KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "celebrity_gossip",
        (
            "actress",
            "actor ",
            "celebrity",
            "reality star",
            "singer ",
            "明星",
            "演员",
            "歌手",
            "绯闻",
        ),
    ),
    (
        "low_value_viral",
        ("goes viral", "viral video", "internet reacts", "网红", "热搜"),
    ),
    (
        "ai_llm_agent",
        (
            "artificial intelligence",
            "openai",
            "anthropic",
            "chatgpt",
            "claude",
            "gemini",
            "deepseek",
            "qwen",
            "language model",
            " ai ",
            " llm",
            "gpt-",
            "人工智能",
            "大模型",
            "智能体",
        ),
    ),
    (
        "finance",
        (
            "stock market",
            "shares ",
            "interest rate",
            "inflation",
            "central bank",
            "earnings",
            "investment",
            "wall street",
            "finance",
            "股市",
            "利率",
            "通胀",
            "央行",
            "财报",
        ),
    ),
    (
        "china",
        ("china", "chinese", "beijing", "中国", "北京", "上海", "深圳"),
    ),
    (
        "uk",
        ("united kingdom", "britain", "british", " uk ", "london", "英国", "伦敦"),
    ),
    (
        "technology",
        (
            "technology",
            "software",
            "developer",
            "github",
            "microsoft",
            "google",
            "apple",
            "meta",
            "instagram",
            "facebook",
            "chip",
            "semiconductor",
            "科技",
            "软件",
            "开发者",
            "芯片",
        ),
    ),
)


def infer_category(text_parts: Iterable[str], fallback: str) -> str:
    haystack = f" {' '.join(text_parts).casefold()} "
    for category, keywords in KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return category
    return fallback
