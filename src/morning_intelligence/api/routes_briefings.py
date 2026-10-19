from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse

from morning_intelligence.tts import AudioGenerationError, AudioUnavailableError

from .auth import require_auth
from .schemas import (
    AudioURLResponse,
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

AUDIO_PREPARATION_RETRY_SECONDS = 5


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


@router.get("/latest/audio", response_class=FileResponse)
async def latest_audio(request: Request) -> FileResponse:
    briefing = request.app.state.service.latest()
    if briefing is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="briefing has not been generated yet",
        )
    try:
        artifact = await request.app.state.audio_service.get_or_create(
            briefing.spoken_text
        )
    except (AudioUnavailableError, AudioGenerationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="briefing audio is unavailable",
        ) from exc
    return FileResponse(artifact.path, media_type="audio/mpeg", filename=None)


@router.get(
    "/latest/audio-url",
    response_model=AudioURLResponse,
    response_model_exclude_none=True,
)
async def latest_audio_url(request: Request, response: Response) -> AudioURLResponse:
    briefing = request.app.state.service.latest()
    if briefing is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="briefing has not been generated yet",
        )
    signer = request.app.state.media_signer
    if not signer.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="media URL signing is not configured",
        )
    try:
        audio_service = request.app.state.audio_service
        artifact = audio_service.get_cached_for_text(briefing.spoken_text)
        if artifact is None:
            audio_service.schedule(briefing.spoken_text)
            artifact = audio_service.get_cached_for_text(briefing.spoken_text)
    except AudioUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="briefing audio is unavailable",
        ) from exc

    if artifact is None:
        response.status_code = status.HTTP_202_ACCEPTED
        response.headers["Retry-After"] = str(AUDIO_PREPARATION_RETRY_SECONDS)
        return AudioURLResponse(
            status="preparing",
            retry_after_seconds=AUDIO_PREPARATION_RETRY_SECONDS,
        )

    expires = int(datetime.now(UTC).timestamp()) + (
        request.app.state.settings.morning_media_url_ttl_seconds
    )
    signature = signer.sign(artifact.cache_id, expires)
    media_url = request.url_for("media_audio", cache_id=artifact.cache_id).include_query_params(
        expires=expires,
        signature=signature,
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto.split(",", maxsplit=1)[0].strip().lower() == "https":
        media_url = media_url.replace(scheme="https")
    return AudioURLResponse(
        status="ready",
        url=str(media_url),
        expires_at=datetime.fromtimestamp(expires, UTC),
    )


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
