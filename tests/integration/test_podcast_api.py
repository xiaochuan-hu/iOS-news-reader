from __future__ import annotations

import hashlib
import struct
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from morning_intelligence.config import Settings
from morning_intelligence.domain import Briefing
from morning_intelligence.main import create_app
from morning_intelligence.tts import AudioService

PODCAST_TOKEN = "p" * 32
ITUNES_NAMESPACE = "http://www.itunes.com/dtds/podcast-1.0.dtd"
AUDIO_BYTES = (b"ID3-private-podcast-test-audio" * 20) + bytes(range(256))


class CacheOnlyBriefingService:
    def __init__(self, *, cached: bool = True) -> None:
        self.briefing = (
            Briefing(
                generated_at=datetime.now(UTC),
                event_count=1,
                estimated_duration_seconds=30,
                spoken_text="这是仅供播客缓存测试使用的晨间简报。",
            )
            if cached
            else None
        )
        self.latest_calls = 0
        self.refresh_calls = 0
        self.generate_calls = 0

    def latest(self) -> Briefing | None:
        self.latest_calls += 1
        return self.briefing

    async def deadline_daily_refresh(self, local_date: date) -> None:
        self.refresh_calls += 1
        raise AssertionError(f"podcast route attempted daily refresh for {local_date}")

    async def generate(self, **kwargs: object) -> Briefing:
        self.generate_calls += 1
        raise AssertionError(f"podcast route attempted generation with {kwargs}")


class NoSynthesisProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def cache_identity(self) -> Mapping[str, str]:
        return {
            "provider": self.name,
            "voice": "test",
            "style": "newscast",
            "rate": "0%",
            "output_format": "test-mp3-v1",
        }

    async def synthesize(self, text: str, output_path: Path) -> None:
        self.calls += 1
        raise AssertionError(f"podcast route attempted TTS for {text!r} at {output_path}")


def make_podcast_client(
    tmp_path: Path,
    *,
    configured_token: str = PODCAST_TOKEN,
    cached_briefing: bool = True,
    cached_audio: bool = True,
) -> tuple[TestClient, CacheOnlyBriefingService, NoSynthesisProvider, str | None]:
    settings = Settings(
        _env_file=None,
        morning_podcast_token=configured_token,
        morning_audio_directory=tmp_path / "audio",
    )
    service = CacheOnlyBriefingService(cached=cached_briefing)
    provider = NoSynthesisProvider()
    audio_service = AudioService(
        provider=provider,
        audio_directory=settings.morning_audio_directory,
    )
    cache_id = None
    if service.briefing is not None:
        cache_id = audio_service.cache_id_for_text(service.briefing.spoken_text)
        if cached_audio:
            settings.morning_audio_directory.mkdir(parents=True)
            (settings.morning_audio_directory / f"{cache_id}.mp3").write_bytes(AUDIO_BYTES)
    app = create_app(
        settings=settings,
        service=service,  # type: ignore[arg-type]
        audio_service=audio_service,
    )
    return TestClient(app), service, provider, cache_id


def test_podcast_feed_hides_missing_and_incorrect_token(tmp_path: Path) -> None:
    disabled_client, _, _, _ = make_podcast_client(tmp_path / "disabled", configured_token="")
    disabled = disabled_client.get(
        "/v1/podcast/anything/freshness-test.xml", params={"start": 1}
    )
    assert disabled.status_code == 404

    client, _, _, _ = make_podcast_client(tmp_path / "enabled")
    incorrect = client.get(
        f"/v1/podcast/{'x' * 32}/freshness-test.xml", params={"start": 1}
    )
    assert incorrect.status_code == 404


def test_podcast_feed_transitions_from_a_to_b_then_c_and_is_stable(
    tmp_path: Path,
) -> None:
    client, service, provider, cache_id = make_podcast_client(tmp_path)
    assert cache_id is not None
    now = int(time.time())
    a_start = now
    a_response = client.get(_feed_path(a_start))

    assert a_response.status_code == 200
    assert a_response.headers["content-type"].startswith("application/rss+xml")
    a_root = ElementTree.fromstring(a_response.content)
    assert [item.findtext("title") for item in a_root.findall("./channel/item")] == [
        "Freshness Test Episode A"
    ]
    assert a_root.findtext("./channel/title") == "Morning Intelligence Freshness Test"
    assert (
        a_root.findtext("./channel/description")
        == "Private freshness test for Morning Intelligence"
    )
    assert a_root.findtext("./channel/language") == "zh-cn"
    assert a_root.findtext(f"./channel/{{{ITUNES_NAMESPACE}}}explicit") == "false"
    assert a_root.findtext(f"./channel/{{{ITUNES_NAMESPACE}}}block") == "Yes"
    category = a_root.find(f"./channel/{{{ITUNES_NAMESPACE}}}category")
    assert category is not None and category.get("text") == "Technology"

    b_start = now - 300
    b_root = ElementTree.fromstring(client.get(_feed_path(b_start)).content)
    assert [item.findtext("title") for item in b_root.findall("./channel/item")] == [
        "Freshness Test Episode B",
        "Freshness Test Episode A",
    ]

    c_start = now - 600
    c_response = client.get(_feed_path(c_start))
    repeated = client.get(_feed_path(c_start))
    c_root = ElementTree.fromstring(c_response.content)
    repeated_root = ElementTree.fromstring(repeated.content)
    items = c_root.findall("./channel/item")

    assert [item.findtext("title") for item in items] == [
        "Freshness Test Episode C",
        "Freshness Test Episode B",
        "Freshness Test Episode A",
    ]
    assert [item.findtext("description") for item in items] == [
        "Morning Intelligence private freshness test episode C.",
        "Morning Intelligence private freshness test episode B.",
        "Morning Intelligence private freshness test episode A.",
    ]
    assert [
        int(parsedate_to_datetime(item.findtext("pubDate", "")).timestamp()) for item in items
    ] == [c_start + 600, c_start + 300, c_start]
    guids = [item.findtext("guid") for item in items]
    repeated_guids = [
        item.findtext("guid") for item in repeated_root.findall("./channel/item")
    ]
    assert guids == repeated_guids
    expected_episode_ids = [
        hashlib.sha256(
            f"morning-podcast-freshness-v1:{c_start}:{cache_id}:{label}".encode()
        ).hexdigest()
        for label in ("C", "B", "A")
    ]
    assert guids == [
        f"morning-intelligence-freshness:{episode_id}"
        for episode_id in expected_episode_ids
    ]
    assert all(item.find("guid").get("isPermaLink") == "false" for item in items)  # type: ignore[union-attr]

    enclosures = [item.find("enclosure") for item in items]
    enclosure_urls = [enclosure.get("url") for enclosure in enclosures if enclosure is not None]
    assert len(enclosure_urls) == 3
    assert len(set(enclosure_urls)) == 3
    assert all(
        enclosure is not None
        and enclosure.get("length")
        == str((tmp_path / "audio" / f"{cache_id}.mp3").stat().st_size)
        and enclosure.get("type") == "audio/mpeg"
        for enclosure in enclosures
    )
    assert service.latest_calls == 4
    assert service.refresh_calls == service.generate_calls == 0
    assert provider.calls == 0


def test_podcast_feed_uses_cache_only_and_reports_unavailable_states(tmp_path: Path) -> None:
    no_briefing, service, provider, _ = make_podcast_client(
        tmp_path / "briefing", cached_briefing=False
    )
    briefing_response = no_briefing.get(_feed_path(1))
    assert briefing_response.status_code == 503
    assert service.refresh_calls == service.generate_calls == provider.calls == 0

    no_audio, service, provider, _ = make_podcast_client(
        tmp_path / "audio", cached_audio=False
    )
    audio_response = no_audio.get(_feed_path(1))
    assert audio_response.status_code == 503
    assert audio_response.json()["detail"] == "podcast audio is not ready"
    assert service.refresh_calls == service.generate_calls == provider.calls == 0


def test_podcast_artwork_and_enclosure_support_get_head_and_range(tmp_path: Path) -> None:
    client, _, provider, _ = make_podcast_client(tmp_path)
    start = int(time.time()) - 301
    root = ElementTree.fromstring(client.get(_feed_path(start)).content)
    artwork_url = root.find(f"./channel/{{{ITUNES_NAMESPACE}}}image").get("href")  # type: ignore[union-attr]
    enclosure_url = root.find("./channel/item/enclosure").get("url")  # type: ignore[union-attr]

    artwork = client.get(artwork_url)
    artwork_head = client.head(artwork_url)
    assert artwork.status_code == artwork_head.status_code == 200
    assert artwork.headers["content-type"] == artwork_head.headers["content-type"] == "image/png"
    assert artwork_head.content == b""
    assert int(artwork_head.headers["content-length"]) == len(artwork.content)
    assert artwork.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", artwork.content[16:24]) == (1400, 1400)
    assert artwork.content[25] == 2

    enclosure = client.get(enclosure_url)
    enclosure_head = client.head(enclosure_url)
    ranged = client.get(enclosure_url, headers={"Range": "bytes=0-99"})
    assert enclosure.status_code == enclosure_head.status_code == 200
    assert enclosure.content == AUDIO_BYTES
    assert enclosure.headers["content-type"] == "audio/mpeg"
    assert enclosure_head.headers["content-type"] == "audio/mpeg"
    assert int(enclosure_head.headers["content-length"]) == len(AUDIO_BYTES)
    assert enclosure_head.content == b""
    assert ranged.status_code == 206
    assert ranged.content == AUDIO_BYTES[:100]
    assert ranged.headers["content-range"] == f"bytes 0-99/{len(AUDIO_BYTES)}"
    assert provider.calls == 0


def test_podcast_feed_asset_urls_honor_forwarded_https_only(tmp_path: Path) -> None:
    client, _, _, _ = make_podcast_client(tmp_path)
    start = int(time.time()) - 301

    local_root = ElementTree.fromstring(client.get(_feed_path(start)).content)
    local_urls = _feed_urls(local_root)
    assert local_urls
    assert all(url.startswith("http://") for url in local_urls)

    forwarded = client.get(_feed_path(start), headers={"X-Forwarded-Proto": "https"})
    forwarded_root = ElementTree.fromstring(forwarded.content)
    forwarded_urls = _feed_urls(forwarded_root)
    assert forwarded_urls
    assert all(url.startswith("https://") for url in forwarded_urls)


def _feed_path(start: int) -> str:
    return f"/v1/podcast/{PODCAST_TOKEN}/freshness-test.xml?start={start}"


def _feed_urls(root: ElementTree.Element) -> list[str]:
    urls = [root.findtext("./channel/link", "")]
    image = root.find(f"./channel/{{{ITUNES_NAMESPACE}}}image")
    if image is not None:
        urls.append(image.get("href", ""))
    urls.extend(
        enclosure.get("url", "")
        for enclosure in root.findall("./channel/item/enclosure")
    )
    return urls
