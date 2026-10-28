from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response

from morning_intelligence.domain import Briefing
from morning_intelligence.tts import (
    AudioArtifact,
    AudioService,
    AudioUnavailableError,
    is_safe_cache_id,
)

from .external_urls import external_url_for

FRESHNESS_DELAY_SECONDS = 300
PRODUCTION_FEED_RETENTION = 7
MAX_START_TIMESTAMP = 253_402_300_499
ITUNES_NAMESPACE = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ARTWORK_PATH = Path(__file__).with_name("assets") / "podcast-freshness-test.png"

ElementTree.register_namespace("itunes", ITUNES_NAMESPACE)


def require_podcast_token(token: str, request: Request) -> None:
    configured = request.app.state.settings.morning_podcast_token
    if not configured or not hmac.compare_digest(
        token.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


router = APIRouter(
    prefix="/v1/podcast/{token}",
    tags=["podcast"],
    dependencies=[Depends(require_podcast_token)],
)


@router.get("/feed.xml", name="podcast_feed")
async def podcast_feed(token: str, request: Request) -> Response:
    feed_url = external_url_for(request, "podcast_feed", token=token)
    root = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(root, "channel")
    _text_element(channel, "title", "Morning Intelligence")
    _text_element(channel, "link", str(feed_url))
    _text_element(
        channel,
        "description",
        "Daily private Morning Intelligence briefing.",
    )
    _text_element(channel, "language", "zh-cn")
    _text_element(channel, _itunes("explicit"), "false")
    _text_element(channel, _itunes("block"), "Yes")

    audio_service = cast(AudioService, request.app.state.audio_service)
    for briefing in request.app.state.service.recent(PRODUCTION_FEED_RETENTION):
        try:
            artifact = audio_service.get_cached_for_text(briefing.spoken_text)
            if artifact is None:
                continue
            byte_length = artifact.path.stat().st_size
        except (AudioUnavailableError, OSError):
            continue
        if byte_length <= 0:
            continue
        _add_production_episode(
            channel,
            request=request,
            token=token,
            briefing=briefing,
            artifact=artifact,
            byte_length=byte_length,
        )

    xml = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(content=xml, media_type="application/rss+xml")


@router.get("/freshness-test.xml", name="podcast_freshness_feed")
async def freshness_test_feed(
    token: str,
    request: Request,
    start: int = Query(..., ge=0, le=MAX_START_TIMESTAMP),
) -> Response:
    artifact = _latest_cached_audio(request)
    try:
        byte_length = artifact.path.stat().st_size
    except OSError as exc:
        raise _audio_not_ready() from exc
    if byte_length <= 0:
        raise _audio_not_ready()

    artwork_url = external_url_for(request, "podcast_artwork", token=token)
    feed_url = external_url_for(
        request, "podcast_freshness_feed", token=token
    ).include_query_params(start=start)
    root = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(root, "channel")
    _text_element(channel, "title", "Morning Intelligence Freshness Test")
    _text_element(channel, "link", str(feed_url))
    _text_element(
        channel,
        "description",
        "Private freshness test for Morning Intelligence",
    )
    _text_element(channel, "language", "zh-cn")
    _text_element(channel, _itunes("explicit"), "false")
    ElementTree.SubElement(channel, _itunes("category"), {"text": "Technology"})
    _text_element(channel, _itunes("block"), "Yes")
    ElementTree.SubElement(channel, _itunes("image"), {"href": str(artwork_url)})

    now = int(datetime.now(UTC).timestamp())
    if now >= start + (2 * FRESHNESS_DELAY_SECONDS):
        labels = ("C", "B", "A")
    elif now >= start + FRESHNESS_DELAY_SECONDS:
        labels = ("B", "A")
    else:
        labels = ("A",)
    for label in labels:
        published_at = start + ({"A": 0, "B": 1, "C": 2}[label] * FRESHNESS_DELAY_SECONDS)
        _add_episode(
            channel,
            request=request,
            token=token,
            cache_id=artifact.cache_id,
            label=label,
            published_at=published_at,
            byte_length=byte_length,
            start=start,
        )

    xml = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(content=xml, media_type="application/rss+xml")


@router.api_route(
    "/media/{cache_id}/{episode_id}.mp3",
    methods=["GET", "HEAD"],
    name="podcast_media",
    response_class=FileResponse,
)
async def podcast_media(
    cache_id: str,
    episode_id: str,
    request: Request,
) -> FileResponse:
    if not is_safe_cache_id(cache_id) or not _is_safe_episode_id(episode_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audio file not found")
    path = request.app.state.audio_service.cached_path(cache_id)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audio file not found")
    return FileResponse(path, media_type="audio/mpeg", filename=None)


@router.api_route(
    "/artwork.png",
    methods=["GET", "HEAD"],
    name="podcast_artwork",
    response_class=FileResponse,
)
async def podcast_artwork() -> FileResponse:
    return FileResponse(ARTWORK_PATH, media_type="image/png", filename=None)


def _latest_cached_audio(request: Request) -> AudioArtifact:
    briefing = request.app.state.service.latest()
    if briefing is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="briefing has not been generated yet",
        )
    try:
        audio_service = cast(AudioService, request.app.state.audio_service)
        artifact = audio_service.get_cached_for_text(briefing.spoken_text)
    except AudioUnavailableError as exc:
        raise _audio_not_ready() from exc
    if artifact is None:
        raise _audio_not_ready()
    return artifact


def _add_episode(
    channel: ElementTree.Element,
    *,
    request: Request,
    token: str,
    cache_id: str,
    label: str,
    published_at: int,
    byte_length: int,
    start: int,
) -> None:
    episode_id = _episode_id(start, cache_id, label)
    enclosure_url = external_url_for(
        request,
        "podcast_media",
        token=token,
        cache_id=cache_id,
        episode_id=episode_id,
    )
    item = ElementTree.SubElement(channel, "item")
    _text_element(item, "title", f"Freshness Test Episode {label}")
    _text_element(
        item,
        "description",
        f"Morning Intelligence private freshness test episode {label}.",
    )
    _text_element(
        item,
        "pubDate",
        format_datetime(datetime.fromtimestamp(published_at, UTC), usegmt=True),
    )
    guid = _text_element(item, "guid", f"morning-intelligence-freshness:{episode_id}")
    guid.set("isPermaLink", "false")
    ElementTree.SubElement(
        item,
        "enclosure",
        {
            "url": str(enclosure_url),
            "length": str(byte_length),
            "type": "audio/mpeg",
        },
    )
    _text_element(item, _itunes("explicit"), "false")


def _add_production_episode(
    channel: ElementTree.Element,
    *,
    request: Request,
    token: str,
    briefing: Briefing,
    artifact: AudioArtifact,
    byte_length: int,
) -> None:
    episode_id = _production_episode_id(briefing.id)
    enclosure_url = external_url_for(
        request,
        "podcast_media",
        token=token,
        cache_id=artifact.cache_id,
        episode_id=episode_id,
    )
    episode_date = briefing.generated_at.date().isoformat()
    published_at = briefing.generated_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    else:
        published_at = published_at.astimezone(UTC)
    item = ElementTree.SubElement(channel, "item")
    _text_element(item, "title", f"Morning Intelligence — {episode_date}")
    _text_element(
        item,
        "description",
        f"Daily private Morning Intelligence briefing for {episode_date}.",
    )
    _text_element(item, "pubDate", format_datetime(published_at, usegmt=True))
    guid = _text_element(item, "guid", f"morning-intelligence:{briefing.id}")
    guid.set("isPermaLink", "false")
    ElementTree.SubElement(
        item,
        "enclosure",
        {
            "url": str(enclosure_url),
            "length": str(byte_length),
            "type": "audio/mpeg",
        },
    )
    _text_element(item, _itunes("explicit"), "false")


def _episode_id(start: int, cache_id: str, label: str) -> str:
    identity = f"morning-podcast-freshness-v1:{start}:{cache_id}:{label}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _production_episode_id(briefing_id: str) -> str:
    identity = f"morning-podcast-v1:{briefing_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _is_safe_episode_id(episode_id: str) -> bool:
    return len(episode_id) == 64 and all(
        character in "0123456789abcdef" for character in episode_id
    )


def _itunes(local_name: str) -> str:
    return f"{{{ITUNES_NAMESPACE}}}{local_name}"


def _text_element(
    parent: ElementTree.Element, tag: str, text: str
) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, tag)
    element.text = text
    return element


def _audio_not_ready() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="podcast audio is not ready",
    )
