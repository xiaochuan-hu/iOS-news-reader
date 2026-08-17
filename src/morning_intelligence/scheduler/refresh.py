from __future__ import annotations

import asyncio
import logging

from morning_intelligence.services import MorningBriefingService

logger = logging.getLogger(__name__)


async def refresh_loop(service: MorningBriefingService, interval_minutes: int) -> None:
    while True:
        try:
            await service.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background refresh failed")
        await asyncio.sleep(interval_minutes * 60)
