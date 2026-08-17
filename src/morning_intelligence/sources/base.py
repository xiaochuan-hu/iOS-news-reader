from __future__ import annotations

from datetime import datetime
from typing import Protocol

import httpx

from morning_intelligence.domain import NewsItem


class NewsSource(Protocol):
    name: str

    async def fetch(self, since: datetime, client: httpx.AsyncClient) -> list[NewsItem]: ...
