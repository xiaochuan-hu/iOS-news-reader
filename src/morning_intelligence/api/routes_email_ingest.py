from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .auth import require_email_ingest_auth
from .schemas import EmailIngestRequest, EmailIngestResponse

router = APIRouter(
    prefix="/v1/email-ingest",
    tags=["email-ingest"],
    dependencies=[Depends(require_email_ingest_auth)],
)


@router.post("", response_model=EmailIngestResponse)
async def ingest_email_messages(
    payload: EmailIngestRequest, request: Request
) -> EmailIngestResponse:
    accepted = request.app.state.service.repository.upsert_email_messages(payload.messages)
    return EmailIngestResponse(accepted=accepted)
