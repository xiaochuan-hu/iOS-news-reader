from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from morning_intelligence.domain import Briefing
from morning_intelligence.storage import BriefingRepository

logger = logging.getLogger(__name__)


class RefreshService(Protocol):
    repository: BriefingRepository

    async def refresh(self) -> Briefing: ...


def next_refresh_at(
    now: datetime,
    timezone_name: str,
    refresh_time_local: str,
) -> datetime:
    """Return the next valid daily local run, normalized across DST transitions."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    timezone = ZoneInfo(timezone_name)
    hour, minute = (int(part) for part in refresh_time_local.split(":"))
    local_now = now.astimezone(timezone)
    candidate = _normalized_local_datetime(
        local_now.date(), time(hour=hour, minute=minute), timezone
    )
    if candidate.astimezone(UTC) <= now.astimezone(UTC):
        candidate = _normalized_local_datetime(
            local_now.date() + timedelta(days=1),
            time(hour=hour, minute=minute),
            timezone,
        )
    return candidate


def _normalized_local_datetime(day: date, local_time: time, timezone: ZoneInfo) -> datetime:
    candidate = datetime.combine(day, local_time, tzinfo=timezone)
    return candidate.astimezone(UTC).astimezone(timezone)


async def refresh_loop(
    service: RefreshService,
    timezone_name: str,
    refresh_time_local: str,
    *,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    current_time = now or (lambda: datetime.now(UTC))
    if service.repository.get_latest_briefing() is None:
        await _refresh_without_stopping(service)

    while True:
        current = current_time()
        scheduled = next_refresh_at(current, timezone_name, refresh_time_local)
        delay = max(
            0.0,
            (scheduled.astimezone(UTC) - current.astimezone(UTC)).total_seconds(),
        )
        await sleep(delay)
        await _refresh_without_stopping(service)


async def _refresh_without_stopping(service: RefreshService) -> None:
    try:
        await service.refresh()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("background refresh failed")
