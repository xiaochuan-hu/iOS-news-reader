from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from morning_intelligence.domain import (
    Briefing,
    GitHubTrendingRepo,
    LLMBudgetClaim,
    LLMBudgetClaimResult,
    NewsEvent,
    NewsItem,
    PipelineMetrics,
)


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

                CREATE TABLE IF NOT EXISTS llm_daily_budget (
                    id TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    claimed_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT,
                    success INTEGER,
                    error TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    UNIQUE(purpose, local_date)
                );
                CREATE INDEX IF NOT EXISTS idx_llm_budget_purpose_claimed
                    ON llm_daily_budget(purpose, claimed_at_utc DESC);

                CREATE TABLE IF NOT EXISTS github_trending_snapshots (
                    snapshot_date TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    fetched_at_utc TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(snapshot_date, rank)
                );

                """
            )

    def save_github_trending_snapshot(
        self,
        snapshot_date: date,
        repositories: list[GitHubTrendingRepo],
        *,
        fetched_at_utc: datetime,
    ) -> None:
        rows = [
            (
                snapshot_date.isoformat(),
                repository.rank,
                fetched_at_utc.astimezone(UTC).isoformat(),
                repository.model_dump_json(),
            )
            for repository in repositories
        ]
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM github_trending_snapshots WHERE snapshot_date = ?",
                (snapshot_date.isoformat(),),
            )
            connection.executemany(
                "INSERT INTO github_trending_snapshots"
                "(snapshot_date, rank, fetched_at_utc, payload) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )

    def get_github_trending_snapshot(
        self, snapshot_date: date
    ) -> list[GitHubTrendingRepo]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM github_trending_snapshots "
                "WHERE snapshot_date = ? ORDER BY rank",
                (snapshot_date.isoformat(),),
            ).fetchall()
        return [GitHubTrendingRepo.model_validate_json(row["payload"]) for row in rows]

    def claim_llm_budget(
        self,
        *,
        purpose: str,
        local_date: date,
        now_utc: datetime,
        minimum_interval: timedelta,
        provider: str,
        model: str,
    ) -> LLMBudgetClaimResult:
        """Atomically consume a fail-closed allowance before any outbound LLM request."""
        if now_utc.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        claimed_at = now_utc.astimezone(UTC)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            same_day = connection.execute(
                "SELECT * FROM llm_daily_budget WHERE purpose = ? AND local_date = ?",
                (purpose, local_date.isoformat()),
            ).fetchone()
            if same_day is not None:
                connection.rollback()
                return LLMBudgetClaimResult(
                    claimed=False,
                    reason="already_claimed",
                    claim=_budget_claim_from_row(same_day),
                )

            latest = connection.execute(
                "SELECT * FROM llm_daily_budget WHERE purpose = ? "
                "ORDER BY claimed_at_utc DESC LIMIT 1",
                (purpose,),
            ).fetchone()
            if latest is not None:
                previous = _budget_claim_from_row(latest)
                elapsed = claimed_at - previous.claimed_at_utc.astimezone(UTC)
                if elapsed < minimum_interval:
                    connection.rollback()
                    return LLMBudgetClaimResult(
                        claimed=False,
                        reason="min_interval",
                        claim=previous,
                    )

            claim = LLMBudgetClaim(
                id=uuid4().hex,
                purpose=purpose,
                local_date=local_date,
                claimed_at_utc=claimed_at,
                provider=provider,
                model=model,
            )
            connection.execute(
                """INSERT INTO llm_daily_budget(
                    id, purpose, local_date, claimed_at_utc, provider, model
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    claim.id,
                    claim.purpose,
                    claim.local_date.isoformat(),
                    claim.claimed_at_utc.isoformat(),
                    claim.provider,
                    claim.model,
                ),
            )
            connection.commit()
            return LLMBudgetClaimResult(claimed=True, reason="claimed", claim=claim)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_llm_budget_claim(
        self,
        claim_id: str,
        *,
        success: bool,
        error: str,
        completed_at_utc: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE llm_daily_budget
                SET completed_at_utc = ?, success = ?, error = ? WHERE id = ?""",
                (
                    completed_at_utc.astimezone(UTC).isoformat(),
                    int(success),
                    error[:500],
                    claim_id,
                ),
            )

    def save_items(self, items: list[NewsItem]) -> None:
        now = _now_iso()
        rows = [
            (
                item.id,
                item.canonical_url,
                item.published_at.isoformat() if item.published_at else "",
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


def _budget_claim_from_row(row: sqlite3.Row) -> LLMBudgetClaim:
    return LLMBudgetClaim(
        id=str(row["id"]),
        purpose=str(row["purpose"]),
        local_date=datetime.strptime(str(row["local_date"]), "%Y-%m-%d").date(),
        claimed_at_utc=_parse(str(row["claimed_at_utc"])),
        completed_at_utc=(
            _parse(str(row["completed_at_utc"])) if row["completed_at_utc"] else None
        ),
        success=bool(row["success"]) if row["success"] is not None else None,
        error=str(row["error"]),
        provider=str(row["provider"]),
        model=str(row["model"]),
    )
