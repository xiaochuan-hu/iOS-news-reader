from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from morning_intelligence.briefing import BriefingGenerator
from morning_intelligence.briefing.spoken_script import deterministic_spoken_script
from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import (
    EnrichedNewsEvent,
    EventIntelligence,
    NewsEvent,
    NewsItem,
    SourceType,
)
from morning_intelligence.llm import LLMProvider, parse_event_intelligence, parse_json_object
from morning_intelligence.llm.prompts import EVENT_SYNTHESIS_SYSTEM_PROMPT
from morning_intelligence.processing import (
    EditorialSelector,
    canonicalize_url,
    cluster_items,
    deduplicate_items,
    normalize_item,
    score_event,
)
from morning_intelligence.services import CollectionResult, SourceOutcome

CASES = Path(__file__).with_name("cases.yaml")


def run_all(path: Path = CASES) -> list[dict[str, Any]]:
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [_run(case) for case in cases]


def _run(case: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    kind = case["type"]
    passed = False
    detail = ""
    if kind == "cluster":
        events = cluster_items(
            [normalize_item(_item(**row)) for row in case["items"]],
            threshold=case["threshold"],
        )
        passed = len(events) == case["expected_events"]
        detail = f"events={len(events)}"
    elif kind == "url":
        values = [canonicalize_url(url) for url in case["urls"]]
        passed = (len(set(values)) == 1) is case["expected_equal"]
        detail = json.dumps(values)
    elif kind == "partial_failure":
        outcomes = [
            SourceOutcome(name=str(index), succeeded=value == "success")
            for index, value in enumerate(case["outcomes"])
        ]
        usable = not CollectionResult(outcomes).all_failed
        passed = usable is case["expected_usable"]
        detail = f"usable={usable}"
    elif kind == "spoken":
        events = [_event(title, index) for index, title in enumerate(case["titles"])]
        script = deterministic_spoken_script(events, now=datetime.now(UTC))
        phrase_ok = case.get("expected_phrase", "") in script
        if "expected_phrase" not in case:
            phrase_ok = True
        passed = bool(script) is case["expected_nonempty"] if "expected_nonempty" in case else True
        passed = passed and phrase_ok and len(script) <= case["max_chars"]
        passed = passed and not any(symbol in script for symbol in ("#", "*", "http"))
        detail = f"chars={len(script)}"
    elif kind == "novelty":
        fresh, repeat = _event("Repeated event", 1), _event("Repeated event", 2)
        preferences = PreferencesConfig()
        score_event(fresh, preferences, was_recently_spoken=False)
        score_event(repeat, preferences, was_recently_spoken=True)
        passed = fresh.final_score > repeat.final_score
        detail = f"fresh={fresh.final_score}, repeat={repeat.final_score}"
    elif kind == "latency":
        items = [
            normalize_item(_item(f"Story number {index}", f"https://example.com/{index}", "Feed"))
            for index in range(case["item_count"])
        ]
        cluster_items(deduplicate_items(items), threshold=76)
        elapsed_ms = (time.perf_counter() - started) * 1000
        passed = elapsed_ms < case["threshold_ms"]
        detail = f"elapsed_ms={elapsed_ms:.2f}"
    elif kind == "structured_chinese":
        parsed = parse_event_intelligence(
            {"events": case["rows"]}, expected_ids=set(case["expected_ids"])
        )
        combined = "".join(value.summary_zh for value in parsed.values())
        chinese = sum("\u3400" <= character <= "\u9fff" for character in combined)
        passed = set(parsed) == set(case["expected_ids"]) and chinese >= case["min_chinese"]
        detail = f"events={len(parsed)}, chinese_chars={chinese}"
    elif kind == "editorial":
        preferences = PreferencesConfig.model_validate(
            {
                "briefing": {
                    "min_events": 1,
                    "max_events": case["max_events"],
                },
                "editorial": {"minimum_score": case["minimum_score"]},
            }
        )
        candidates = [
            _editorial_event(row, index) for index, row in enumerate(case["candidates"])
        ]
        selection = EditorialSelector(preferences).select(candidates)
        max_category_ok = all(
            selection.selected_categories.get(category, 0) <= maximum
            for category, maximum in case.get("max_category_counts", {}).items()
        )
        required_ok = all(
            selection.selected_categories.get(category, 0) >= minimum
            for category, minimum in case.get("required_category_counts", {}).items()
        )
        forbidden_ok = all(
            category not in selection.selected_categories
            for category in case.get("forbidden_categories", [])
        )
        passed = max_category_ok and required_ok and forbidden_ok
        detail = json.dumps(selection.selected_categories, ensure_ascii=False)
    elif kind == "malformed_json":
        try:
            parse_json_object(case["text"])
        except (ValueError, json.JSONDecodeError):
            passed = case["expected_error"]
        detail = "error_detected" if passed else "error_not_detected"
    elif kind == "timeout_fallback":
        result = asyncio.run(
            BriefingGenerator(_TimeoutLLM()).generate(
                [_event("English source event", 1)],
                target_minutes=5,
                language="zh-CN",
            )
        )
        passed = result.fallback_used and bool(result.briefing.spoken_text)
        detail = result.fallback_reason
    elif kind == "grounding_prompt":
        passed = all(phrase in EVENT_SYNTHESIS_SYSTEM_PROMPT for phrase in case["phrases"])
        detail = f"rules={len(case['phrases'])}"
    elif kind == "length_control":
        events = [_event(f"事件{index}", index) for index in range(case["event_count"])]
        for event in events:
            event.why_it_matters = "它可能影响相关行业接下来的决策"
            event.source_count = 2
        short = deterministic_spoken_script(
            events, now=datetime.now(UTC), target_minutes=case["short_minutes"]
        )
        long = deterministic_spoken_script(
            events, now=datetime.now(UTC), target_minutes=case["long_minutes"]
        )
        passed = len(long) > len(short)
        detail = f"short={len(short)}, long={len(long)}"
    else:
        raise ValueError(f"unknown eval type: {kind}")
    return {
        "id": case["id"],
        "passed": passed,
        "detail": detail,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _item(title: str, url: str, source: str) -> NewsItem:
    return NewsItem(
        source=source,
        source_type=SourceType.RSS,
        title=title,
        url=url,
        published_at=datetime.now(UTC) - timedelta(minutes=10),
        category="technology",
        source_quality=0.8,
    )


def _event(title: str, index: int) -> NewsEvent:
    item = _item(title, f"https://example.com/{index}", f"Feed {index}")
    return NewsEvent(
        canonical_title=title,
        items=[item],
        first_seen_at=item.published_at,
        latest_seen_at=item.published_at,
        categories=["technology"],
    )


def _editorial_event(row: dict[str, Any], index: int) -> NewsEvent:
    category = str(row["category"])
    event = _event(str(row.get("title", f"{category}-{index}")), index)
    event.categories = [category]
    event.global_importance_score = float(row["global"])
    event.personal_interest_score = float(row.get("personal", 0.67))
    return event


class _TimeoutLLM(LLMProvider):
    async def score_events(self, events: list[NewsEvent]) -> dict[str, float]:
        raise TimeoutError

    async def synthesize_events(
        self, events: list[NewsEvent]
    ) -> dict[str, EventIntelligence]:
        raise TimeoutError

    async def create_spoken_script(
        self,
        events: list[EnrichedNewsEvent],
        *,
        target_minutes: int,
        language: str,
        characters_per_minute: int,
    ) -> str:
        raise TimeoutError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = run_all()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}: {result['detail']}")
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
