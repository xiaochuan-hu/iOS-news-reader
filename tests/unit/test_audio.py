from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from morning_intelligence.tts import (
    AudioGenerationError,
    AudioService,
    FFmpegMP3Concatenator,
    chunk_spoken_text,
)


class FakeTTSProvider:
    name = "fake"

    def __init__(
        self,
        *,
        voice: str = "voice-a",
        style: str = "newscast",
        rate: str = "0%",
        fail: bool = False,
        delay: float = 0,
    ) -> None:
        self.voice = voice
        self.style = style
        self.rate = rate
        self.fail = fail
        self.delay = delay
        self.calls = 0

    @property
    def cache_identity(self) -> Mapping[str, str]:
        return {
            "provider": self.name,
            "voice": self.voice,
            "style": self.style,
            "rate": self.rate,
            "output_format": "test-mp3-v1",
        }

    async def synthesize(self, text: str, output_path: Path) -> None:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("simulated TTS failure")
        await asyncio.to_thread(output_path.write_bytes, b"segment:" + text.encode())


class FakeConcatenator:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def concatenate(self, segments: list[Path], output_path: Path) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated ffmpeg failure")
        payloads = await asyncio.gather(
            *(asyncio.to_thread(segment.read_bytes) for segment in segments)
        )
        await asyncio.to_thread(output_path.write_bytes, b"".join(payloads))


class BlockingTTSProvider(FakeTTSProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def synthesize(self, text: str, output_path: Path) -> None:
        del text, output_path
        self.calls += 1
        self.started.set()
        await asyncio.Event().wait()


class StubbornFakeProcess:
    def __init__(self) -> None:
        self.communication_started = asyncio.Event()
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communication_started.set()
        await asyncio.Event().wait()
        raise AssertionError("communicate should be cancelled")

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        if not self.killed:
            await asyncio.Event().wait()
        return self.returncode or 0


def make_service(
    directory: Path,
    provider: FakeTTSProvider,
    *,
    concatenator: FakeConcatenator | None = None,
    retention_days: int = 7,
) -> AudioService:
    return AudioService(
        provider=provider,
        audio_directory=directory,
        retention_days=retention_days,
        concatenator=concatenator or FakeConcatenator(),
        chunk_characters=1_000,
    )


async def test_spoken_text_is_cached_and_second_request_does_not_resynthesize(
    tmp_path: Path,
) -> None:
    provider = FakeTTSProvider()
    service = make_service(tmp_path / "audio", provider)

    first = await service.get_or_create("早上好。这是今天的晨报。")
    second = await service.get_or_create("早上好。这是今天的晨报。")

    assert first == second
    assert first.path.read_bytes().startswith(b"segment:")
    assert provider.calls == 1


def test_text_and_tts_configuration_are_part_of_cache_key(tmp_path: Path) -> None:
    original = make_service(tmp_path, FakeTTSProvider())
    changed_voice = make_service(tmp_path, FakeTTSProvider(voice="voice-b"))
    changed_style = make_service(tmp_path, FakeTTSProvider(style="chat"))
    changed_rate = make_service(tmp_path, FakeTTSProvider(rate="+10%"))

    original_id = original.cache_id_for_text("文本甲")

    assert original_id != original.cache_id_for_text("文本乙")
    assert original_id != changed_voice.cache_id_for_text("文本甲")
    assert original_id != changed_style.cache_id_for_text("文本甲")
    assert original_id != changed_rate.cache_id_for_text("文本甲")


async def test_concurrent_requests_synthesize_once(tmp_path: Path) -> None:
    provider = FakeTTSProvider(delay=0.05)
    service = make_service(tmp_path / "audio", provider)

    artifacts = await asyncio.gather(
        *(service.get_or_create("相同的完整播放文本。") for _ in range(12))
    )

    assert len({artifact.cache_id for artifact in artifacts}) == 1
    assert provider.calls == 1


async def test_long_text_is_synthesized_as_multiple_conservative_chunks(
    tmp_path: Path,
) -> None:
    provider = FakeTTSProvider()
    service = make_service(tmp_path / "audio", provider)

    await service.get_or_create("这是一句完整的话。" * 150)

    assert provider.calls >= 2


async def test_shutdown_safely_cancels_retained_background_tasks(tmp_path: Path) -> None:
    provider = BlockingTTSProvider()
    service = make_service(tmp_path / "audio", provider)

    task = service.schedule("关闭应用时可以取消的音频生成。")
    assert task is not None
    await provider.started.wait()
    await service.shutdown()

    assert task.cancelled()
    assert not list(service.audio_directory.glob("*.mp3"))


async def test_ffmpeg_cancellation_terminates_then_kills_stubborn_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = StubbornFakeProcess()

    async def create_process(*args: object, **kwargs: object) -> StubbornFakeProcess:
        del args, kwargs
        await asyncio.sleep(0)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    working_directory = tmp_path / "ffmpeg"
    await asyncio.to_thread(working_directory.mkdir)
    segment = working_directory / "segment-0000.mp3"
    await asyncio.to_thread(segment.write_bytes, b"fake-mp3")
    concatenator = FFmpegMP3Concatenator(termination_timeout_seconds=0.01)

    task = asyncio.create_task(
        concatenator.concatenate([segment], working_directory / "complete.mp3")
    )
    await process.communication_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated is True
    assert process.killed is True
    assert process.wait_calls == 2


@pytest.mark.parametrize("failure", ["provider", "concatenator"])
async def test_failure_never_leaves_final_cache_file(
    tmp_path: Path, failure: str
) -> None:
    provider = FakeTTSProvider(fail=failure == "provider")
    concatenator = FakeConcatenator(fail=failure == "concatenator")
    service = make_service(tmp_path / "audio", provider, concatenator=concatenator)
    cache_id = service.cache_id_for_text("生成必须保持原子性。")

    with pytest.raises(AudioGenerationError):
        await service.get_or_create("生成必须保持原子性。")

    assert not (service.audio_directory / f"{cache_id}.mp3").exists()
    assert not list(service.audio_directory.iterdir())


async def test_retention_removes_expired_audio_but_keeps_current_and_recent(
    tmp_path: Path,
) -> None:
    provider = FakeTTSProvider()
    service = make_service(tmp_path / "audio", provider, retention_days=7)
    service.audio_directory.mkdir()
    expired = service.audio_directory / f"{'a' * 64}.mp3"
    recent = service.audio_directory / f"{'b' * 64}.mp3"
    current = service.audio_directory / f"{'c' * 64}.mp3"
    for path in (expired, recent, current):
        path.write_bytes(b"audio")
    old = time.time() - 8 * 24 * 60 * 60
    os.utime(expired, (old, old))
    os.utime(current, (old, old))

    await service.cleanup_expired(keep_cache_id=current.stem)

    assert not expired.exists()
    assert recent.exists()
    assert current.exists()


def test_chunking_prefers_sentence_boundaries_and_hard_splits_only_long_sentences() -> None:
    text = "第一句。第二句！第三句？"
    assert chunk_spoken_text(text, max_characters=8) == ["第一句。第二句！", "第三句？"]

    long_sentence = "长" * 21 + "。"
    chunks = chunk_spoken_text(long_sentence, max_characters=10)
    assert "".join(chunks) == long_sentence
    assert all(len(chunk) <= 10 for chunk in chunks)
