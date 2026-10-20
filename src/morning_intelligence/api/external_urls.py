from __future__ import annotations

from typing import Any

from fastapi import Request
from starlette.datastructures import URL


def external_url_for(request: Request, name: str, **path_params: Any) -> URL:
    """Build a route URL, honoring Railway's forwarded HTTPS scheme."""
    url = request.url_for(name, **path_params)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto.split(",", maxsplit=1)[0].strip().lower() == "https":
        return url.replace(scheme="https")
    return url
