from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from .auth import require_auth
from .schemas import (
    BriefingResponse,
    DailyRefreshRequest,
    DailyRefreshResponse,
    GenerateBriefingRequest,
    client_local_date_is_valid,
)

router = APIRouter(
    prefix="/v1/briefings",
    tags=["briefings"],
    dependencies=[Depends(require_auth)],
)


@router.get("/latest", response_model=BriefingResponse)
async def latest(request: Request) -> BriefingResponse:
    briefing = request.app.state.service.latest()
    if briefing is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="briefing has not been generated yet",
        )
    return BriefingResponse.from_domain(briefing)


@router.get("/latest/text", response_class=PlainTextResponse)
async def latest_text(request: Request) -> PlainTextResponse:
    briefing = request.app.state.service.latest()
    if briefing is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="briefing has not been generated yet",
        )
    return PlainTextResponse(briefing.spoken_text, media_type="text/plain")


@router.post("/daily-refresh", response_model=DailyRefreshResponse)
async def daily_refresh(
    payload: DailyRefreshRequest, request: Request
) -> DailyRefreshResponse:
    if not client_local_date_is_valid(payload.local_date):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="local_date must be within one calendar day of the current UTC date",
        )
    try:
        result = await request.app.state.service.deadline_daily_refresh(payload.local_date)
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    briefing = result.briefing
    return DailyRefreshResponse(
        status=result.status,
        local_date=result.local_date,
        briefing_id=briefing.id if briefing else None,
        generated_at=briefing.generated_at if briefing else None,
        budget_reason=result.budget_reason,
    )


@router.post("/generate", response_model=BriefingResponse)
async def generate(
    payload: GenerateBriefingRequest, request: Request
) -> BriefingResponse:
    if not request.app.state.settings.morning_manual_generation_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "manual generation is disabled; use the authenticated daily refresh workflow"
            ),
        )
    try:
        briefing = await request.app.state.service.generate(
            target_minutes=payload.target_minutes,
            incremental_refresh=payload.incremental_refresh,
            max_age_minutes=payload.max_age_minutes,
        )
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return BriefingResponse.from_domain(briefing)
