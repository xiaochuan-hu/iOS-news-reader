from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from .auth import require_auth
from .schemas import BriefingResponse, GenerateBriefingRequest

router = APIRouter(
    prefix="/v1/briefings",
    tags=["briefings"],
    dependencies=[Depends(require_auth)],
)


@router.get("/latest", response_model=BriefingResponse)
async def latest(request: Request) -> BriefingResponse:
    try:
        briefing = await request.app.state.service.latest_or_generate()
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return BriefingResponse.from_domain(briefing)


@router.get("/latest/text", response_class=PlainTextResponse)
async def latest_text(request: Request) -> PlainTextResponse:
    try:
        briefing = await request.app.state.service.latest_or_generate()
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return PlainTextResponse(briefing.spoken_text, media_type="text/plain")


@router.post("/generate", response_model=BriefingResponse)
async def generate(
    payload: GenerateBriefingRequest, request: Request
) -> BriefingResponse:
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
