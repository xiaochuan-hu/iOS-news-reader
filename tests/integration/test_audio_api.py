from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from morning_intelligence.config import Settings
from morning_intelligence.domain import Briefing
from morning_intelligence.main import create_app
from morning_intelligence.tts import AudioService, MediaURLSigner

MEDIA_SIGNING_KEY = "m" * 32


class CachedBriefingService:
    def __init__(self) -> None:
        self.briefing = Briefing(
            event_count=1,
            estimated_duration_seconds=30,
            spoken_text="早上好。这是用于音频接口测试的完整晨报。",
        )

    def latest(self) -> Briefing:
        return self.briefing


class APIFakeProvider:
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
        await asyncio.to_thread(output_path.write_bytes, (b"ID3" + text.encode()) * 20)


class APIFakeConcatenator:
    async def concatenate(self, segments: list[Path], output_path: Path) -> None:
        payload = await asyncio.to_thread(
            lambda: b"".join(segment.read_bytes() for segment in segments)
        )
        await asyncio.to_thread(output_path.write_bytes, payload)


class BlockingAPIFakeProvider(APIFakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    async def synthesize(self, text: str, output_path: Path) -> None:
        self.calls += 1
        self.started.set()
        await asyncio.to_thread(self.release.wait)
        await asyncio.to_thread(output_path.write_bytes, (b"ID3" + text.encode()) * 20)


def make_audio_client(
    tmp_path: Path,
    *,
    provider: APIFakeProvider | None = None,
) -> tuple[TestClient, APIFakeProvider]:
    settings = Settings(
        _env_file=None,
        morning_api_token="test-token",
        morning_media_signing_key=MEDIA_SIGNING_KEY,
        morning_audio_directory=tmp_path / "audio",
    )
    runtime_provider = provider or APIFakeProvider()
    audio_service = AudioService(
        provider=runtime_provider,
        audio_directory=settings.morning_audio_directory,
        concatenator=APIFakeConcatenator(),
    )
    app = create_app(
        settings=settings,
        service=CachedBriefingService(),  # type: ignore[arg-type]
        audio_service=audio_service,
    )
    return TestClient(app), runtime_provider


def test_signed_url_streaming_authentication_expiry_and_range(tmp_path: Path) -> None:
    client, provider = make_audio_client(tmp_path)
    headers = {"Authorization": "Bearer test-token"}

    unauthenticated = client.get("/v1/briefings/latest/audio-url")
    assert unauthenticated.status_code == 401

    generated = client.get("/v1/briefings/latest/audio", headers=headers)
    assert generated.status_code == 200
    response = client.get("/v1/briefings/latest/audio-url", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["content_type"] == "audio/mpeg"
    assert "test-token" not in payload["url"]
    assert provider.calls == 1

    streamed = client.get(payload["url"])
    assert streamed.status_code == 200
    assert streamed.headers["content-type"] == "audio/mpeg"

    head = client.head(payload["url"])
    assert head.status_code == 200
    assert head.headers["content-type"] == "audio/mpeg"
    assert int(head.headers["content-length"]) > 0
    if "accept-ranges" in head.headers:
        assert head.headers["accept-ranges"] == "bytes"
    assert head.content == b""

    ranged = client.get(payload["url"], headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"].startswith("bytes 0-99/")
    assert len(ranged.content) == 100

    parsed = urlparse(payload["url"])
    query = parse_qs(parsed.query)
    bad_signature = client.get(
        parsed.path,
        params={"expires": query["expires"][0], "signature": "0" * 64},
    )
    assert bad_signature.status_code == 403
    bad_signature_head = client.head(
        parsed.path,
        params={"expires": query["expires"][0], "signature": "0" * 64},
    )
    assert bad_signature_head.status_code == 403
    malformed_signature = client.get(
        parsed.path,
        params={"expires": query["expires"][0], "signature": "bad"},
    )
    assert malformed_signature.status_code == 403

    cache_id = parsed.path.rsplit("/", 1)[-1]
    expired_at = int(datetime.now(UTC).timestamp()) - 1
    expired_signature = MediaURLSigner(MEDIA_SIGNING_KEY).sign(cache_id, expired_at)
    expired = client.get(
        parsed.path,
        params={"expires": expired_at, "signature": expired_signature},
    )
    assert expired.status_code == 403
    expired_head = client.head(
        parsed.path,
        params={"expires": expired_at, "signature": expired_signature},
    )
    assert expired_head.status_code == 403


def test_audio_url_returns_preparing_without_waiting_for_blocked_tts(
    tmp_path: Path,
) -> None:
    blocking_provider = BlockingAPIFakeProvider()
    client, provider = make_audio_client(tmp_path, provider=blocking_provider)
    headers = {"Authorization": "Bearer test-token"}

    with client:
        try:
            response = client.get("/v1/briefings/latest/audio-url", headers=headers)

            assert response.status_code == 202
            assert response.headers["retry-after"] == "5"
            assert response.json() == {
                "status": "preparing",
                "content_type": "audio/mpeg",
                "retry_after_seconds": 5,
            }
            assert blocking_provider.started.wait(timeout=1)
            assert not blocking_provider.release.is_set()
            assert provider.calls == 1
        finally:
            blocking_provider.release.set()


def test_media_route_rejects_unsafe_cache_ids(tmp_path: Path) -> None:
    client, _ = make_audio_client(tmp_path)

    unsafe = client.get(
        "/v1/media/audio/not-a-cache-id",
        params={"expires": 9_999_999_999, "signature": "0" * 64},
    )
    traversal = client.get(
        "/v1/media/audio/%2E%2E%2Fetc%2Fpasswd",
        params={"expires": 9_999_999_999, "signature": "0" * 64},
    )

    assert unsafe.status_code == 403
    assert traversal.status_code in {403, 404}


def test_direct_authenticated_audio_uses_same_cache(tmp_path: Path) -> None:
    client, provider = make_audio_client(tmp_path)
    headers = {"Authorization": "Bearer test-token"}

    first = client.get("/v1/briefings/latest/audio", headers=headers)
    second = client.get("/v1/briefings/latest/audio", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert provider.calls == 1
