from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from morning_intelligence.domain import Briefing
from morning_intelligence.scheduler import next_refresh_at, refresh_loop


def _briefing() -> Briefing:
    return Briefing(
        event_count=1,
        estimated_duration_seconds=10,
        spoken_text="已缓存晨报",
    )


class StubService:
    def __init__(self, briefing: Briefing | None, *, fail: bool = False) -> None:
        self.briefing = briefing
        self.fail = fail
        self.refresh_calls = 0
        self.local_dates = []

    async def daily_refresh(self, local_date) -> object:
        self.refresh_calls += 1
        self.local_dates.append(local_date)
        if self.fail:
            raise RuntimeError("temporary refresh failure")
        briefing = _briefing()
        self.briefing = briefing
        return briefing


def test_calculates_next_run_on_same_local_day() -> None:
    now = datetime(2026, 1, 15, 4, 30, tzinfo=UTC)

    scheduled = next_refresh_at(now, "Europe/London", "06:00")

    assert scheduled.isoformat() == "2026-01-15T06:00:00+00:00"


def test_next_run_rolls_over_to_following_day() -> None:
    now = datetime(2026, 1, 15, 6, 0, tzinfo=UTC)

    scheduled = next_refresh_at(now, "Europe/London", "06:00")

    assert scheduled.isoformat() == "2026-01-16T06:00:00+00:00"


def test_europe_london_schedule_tracks_dst_offset() -> None:
    before_spring_change = datetime(2026, 3, 28, 7, 0, tzinfo=UTC)

    scheduled = next_refresh_at(before_spring_change, "Europe/London", "06:00")

    assert scheduled.isoformat() == "2026-03-29T06:00:00+01:00"
    assert scheduled.astimezone(UTC).hour == 5


def test_nonexistent_dst_wall_time_normalizes_to_next_valid_time() -> None:
    now = datetime(2026, 3, 28, 7, 0, tzinfo=UTC)

    scheduled = next_refresh_at(now, "Europe/London", "01:30")

    assert scheduled.isoformat() == "2026-03-29T02:30:00+01:00"


async def test_empty_database_never_refreshes_immediately_on_startup() -> None:
    service = StubService(None)

    async def cancel_sleep(_: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await refresh_loop(
            service, "Europe/London", "06:00", sleep=cancel_sleep
        )  # type: ignore[arg-type]

    assert service.refresh_calls == 0


async def test_existing_briefing_skips_immediate_startup_refresh() -> None:
    service = StubService(_briefing())

    async def cancel_sleep(_: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await refresh_loop(
            service, "Europe/London", "06:00", sleep=cancel_sleep
        )  # type: ignore[arg-type]

    assert service.refresh_calls == 0


async def test_refresh_exception_does_not_stop_scheduler() -> None:
    service = StubService(_briefing(), fail=True)
    sleeps = 0

    async def sleep_once_then_cancel(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await refresh_loop(
            service,
            "Europe/London",
            "06:00",
            now=lambda: datetime(2026, 1, 15, 7, tzinfo=UTC),
            sleep=sleep_once_then_cancel,
        )  # type: ignore[arg-type]

    assert service.refresh_calls == 1
    assert service.local_dates == [datetime(2026, 1, 16, tzinfo=UTC).date()]


async def test_refresh_cancellation_propagates() -> None:
    service = StubService(None)

    async def cancelled_refresh(local_date) -> object:
        raise asyncio.CancelledError

    service.daily_refresh = cancelled_refresh  # type: ignore[method-assign]

    async def immediate_sleep(_: float) -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        await refresh_loop(
            service, "Europe/London", "06:00", sleep=immediate_sleep
        )  # type: ignore[arg-type]
