from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


async def require_auth(request: Request) -> None:
    expected = request.app.state.settings.morning_api_token
    if not expected:
        return
    authorization = request.headers.get("Authorization", "")
    header_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    query_token = request.query_params.get("token", "")
    supplied = header_token or query_token
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
