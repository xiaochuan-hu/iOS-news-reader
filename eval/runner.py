from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from morning_intelligence.briefing.spoken_script import deterministic_spoken_script
from morning_intelligence.config import PreferencesConfig
from morning_intelligence.domain import NewsEvent, NewsItem, SourceType
from morning_intelligence.processing import (
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
