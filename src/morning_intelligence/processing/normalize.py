from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from morning_intelligence.domain import NewsItem

from .categorize import infer_category

TRACKING_PARAMETERS = {
    "_ga",
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
    "ttclid",
    "twclid",
}


def canonicalize_url(url: str) -> str:
    """Build a conservative canonical URL while preserving meaningful parameters."""
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).casefold()
    text = re.sub(r"[^\w\u3400-\u9fff]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_item(item: NewsItem) -> NewsItem:
    normalized = item.model_copy(deep=True)
    normalized.title = " ".join(unicodedata.normalize("NFKC", item.title).split())
    normalized.description = re.sub(r"<[^>]+>", " ", item.description or "")
    normalized.description = " ".join(normalized.description.split())
    normalized.canonical_url = canonicalize_url(item.url)
    normalized.category = infer_category(
        (normalized.title, normalized.description), normalized.category
    )
    return normalized
