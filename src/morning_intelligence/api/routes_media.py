from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

router = APIRouter(prefix="/v1/media", tags=["media"])


@router.api_route(
    "/audio/{cache_id}",
    methods=["GET", "HEAD"],
    name="media_audio",
    response_class=FileResponse,
)
async def media_audio(
    cache_id: str,
    request: Request,
    expires: int = Query(..., ge=0),
    signature: str = Query(...),
) -> FileResponse:
    signer = request.app.state.media_signer
    if not signer.verify(cache_id, expires, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid or expired media URL",
        )
    path = request.app.state.audio_service.cached_path(cache_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="audio file not found",
        )
    return FileResponse(path, media_type="audio/mpeg", filename=None)
