from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from morning_intelligence.domain import Briefing


def briefing_response(briefing: Briefing) -> dict[str, Any]:
    freshness = max(
        0,
        round((datetime.now(UTC) - briefing.generated_at).total_seconds() / 60),
    )
    return {
        **briefing.model_dump(mode="json"),
        "freshness_minutes": freshness,
    }
