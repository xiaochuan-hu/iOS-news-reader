from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from morning_intelligence.domain import Briefing, NewsEvent, NewsItem, PipelineMetrics


class SQLiteRepository:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    saved_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_items_published ON news_items(published_at);

                CREATE TABLE IF NOT EXISTS news_events (
                    id TEXT PRIMARY KEY,
                    canonical_title TEXT NOT NULL,
                    latest_seen_at TEXT NOT NULL,
                    final_score REAL NOT NULL,
                    payload TEXT NOT NULL,
                    saved_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_saved ON news_events(saved_at);

                CREATE TABLE IF NOT EXISTS briefings (
                    id TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    spoken_text TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_briefings_generated ON briefings(generated_at);

                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def save_items(self, items: list[NewsItem]) -> None:
        now = _now_iso()
        rows = [
            (
                item.id,
                item.canonical_url,
                item.published_at.isoformat(),
                item.model_dump_json(),
                now,
            )
            for item in items
        ]
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO news_items(id, canonical_url, published_at, payload, saved_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET canonical_url=excluded.canonical_url,
                published_at=excluded.published_at, payload=excluded.payload,
                saved_at=excluded.saved_at""",
                rows,
            )

    def save_events(self, events: list[NewsEvent]) -> None:
        now = _now_iso()
        rows = [
            (
                event.id,
                event.canonical_title,
                event.latest_seen_at.isoformat(),
                event.final_score,
                event.model_dump_json(),
                now,
            )
            for event in events
        ]
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO news_events(
                    id, canonical_title, latest_seen_at, final_score, payload, saved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET canonical_title=excluded.canonical_title,
                latest_seen_at=excluded.latest_seen_at, final_score=excluded.final_score,
                payload=excluded.payload, saved_at=excluded.saved_at""",
                rows,
            )

    def get_latest_events(self) -> tuple[list[NewsEvent], datetime | None]:
        with self._connect() as connection:
            latest = connection.execute(
                "SELECT MAX(saved_at) AS saved_at FROM news_events"
            ).fetchone()
            if latest is None or latest["saved_at"] is None:
                return [], None
            saved_at = str(latest["saved_at"])
            rows = connection.execute(
                "SELECT payload FROM news_events WHERE saved_at = ? ORDER BY final_score DESC",
                (saved_at,),
            ).fetchall()
        return [NewsEvent.model_validate_json(row["payload"]) for row in rows], _parse(saved_at)

    def save_briefing(self, briefing: Briefing) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO briefings(id, generated_at, spoken_text, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    briefing.id,
                    briefing.generated_at.isoformat(),
                    briefing.spoken_text,
                    briefing.model_dump_json(),
                ),
            )

    def get_latest_briefing(self) -> Briefing | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM briefings ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        return Briefing.model_validate_json(row["payload"]) if row else None

    def save_pipeline_run(self, metrics: PipelineMetrics) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO pipeline_runs(
                    run_id, started_at, completed_at, status, payload
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    metrics.run_id,
                    metrics.started_at.isoformat(),
                    metrics.completed_at.isoformat() if metrics.completed_at else None,
                    metrics.status.value,
                    metrics.model_dump_json(),
                ),
            )

    def recent_spoken_titles(self, days: int) -> list[str]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM briefings WHERE generated_at >= ? ORDER BY generated_at DESC",
                (cutoff,),
            ).fetchall()
        event_ids: set[str] = set()
        for row in rows:
            payload = json.loads(row["payload"])
            event_ids.update(payload.get("event_ids", []))
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT canonical_title FROM news_events WHERE id IN ({placeholders})",  # noqa: S608
                tuple(event_ids),
            ).fetchall()
        return [str(row["canonical_title"]) for row in rows]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)
