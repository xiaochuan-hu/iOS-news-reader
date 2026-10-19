from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .base import AudioConcatenator, TTSProvider
from .signing import is_safe_cache_id

logger = logging.getLogger(__name__)

AUDIO_CACHE_VERSION = "morning-audio-v1"
DEFAULT_CHUNK_CHARACTERS = 1_000
DEFAULT_FFMPEG_TERMINATION_TIMEOUT_SECONDS = 2.0


class AudioUnavailableError(RuntimeError):
    pass


class AudioGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioArtifact:
    cache_id: str
    path: Path


class FFmpegMP3Concatenator:
    def __init__(
        self,
        *,
        termination_timeout_seconds: float = DEFAULT_FFMPEG_TERMINATION_TIMEOUT_SECONDS,
    ) -> None:
        if termination_timeout_seconds <= 0:
            raise ValueError("termination_timeout_seconds must be positive")
        self.termination_timeout_seconds = termination_timeout_seconds

    async def concatenate(self, segments: list[Path], output_path: Path) -> None:
        if not segments:
            raise AudioGenerationError("no audio segments were generated")
        working_directory = output_path.parent
        manifest = working_directory / "segments.txt"
        manifest.write_text(
            "".join(f"file '{segment.name}'\n" for segment in segments),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            manifest.name,
            "-c",
            "copy",
            "-y",
            output_path.name,
            cwd=working_directory,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await process.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self.termination_timeout_seconds,
                )
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            raise
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise AudioGenerationError(f"ffmpeg concatenation failed: {detail[:300]}")
        if not await asyncio.to_thread(_is_nonempty_file, output_path):
            raise AudioGenerationError("ffmpeg produced no audio output")


class AudioService:
    def __init__(
        self,
        *,
        provider: TTSProvider | None,
        audio_directory: Path,
        retention_days: int = 7,
        concatenator: AudioConcatenator | None = None,
        chunk_characters: int = DEFAULT_CHUNK_CHARACTERS,
    ) -> None:
        if chunk_characters < 1:
            raise ValueError("chunk_characters must be positive")
        self.provider = provider
        self.audio_directory = audio_directory
        self.retention = timedelta(days=retention_days)
        self.concatenator = concatenator or FFmpegMP3Concatenator()
        self.chunk_characters = chunk_characters
        self._locks: dict[str, asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._preparation_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def available(self) -> bool:
        return self.provider is not None

    def cache_id_for_text(self, text: str) -> str:
        if self.provider is None:
            raise AudioUnavailableError("text-to-speech is not configured")
        canonical = json.dumps(
            {
                "version": AUDIO_CACHE_VERSION,
                "text": text,
                **dict(self.provider.cache_identity),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def cached_path(self, cache_id: str) -> Path | None:
        if not is_safe_cache_id(cache_id):
            return None
        path = self.audio_directory / f"{cache_id}.mp3"
        return path if path.is_file() else None

    def get_cached_for_text(self, text: str) -> AudioArtifact | None:
        """Return existing audio for final text without performing synthesis."""
        if not text.strip():
            raise AudioUnavailableError("briefing has no spoken text")
        cache_id = self.cache_id_for_text(text)
        path = self.cached_path(cache_id)
        return AudioArtifact(cache_id, path) if path is not None else None

    async def get_or_create(self, text: str) -> AudioArtifact:
        if not text.strip():
            raise AudioUnavailableError("briefing has no spoken text")
        cache_id = self.cache_id_for_text(text)
        artifact = self.get_cached_for_text(text)
        if artifact is not None:
            return artifact

        lock = self._locks.setdefault(cache_id, asyncio.Lock())
        async with lock:
            cached = self.cached_path(cache_id)
            if cached is not None:
                return AudioArtifact(cache_id, cached)
            return await self._generate(text, cache_id)

    def schedule(self, text: str) -> asyncio.Task[None] | None:
        if self.provider is None or not text.strip():
            return None
        cache_id = self.cache_id_for_text(text)
        if self.cached_path(cache_id) is not None:
            return None
        existing = self._preparation_tasks.get(cache_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(
            self._prepare_in_background(text),
            name=f"morning-audio-{cache_id[:12]}",
        )
        self._background_tasks.add(task)
        self._preparation_tasks[cache_id] = task

        def discard(completed: asyncio.Task[None]) -> None:
            self._background_tasks.discard(completed)
            if self._preparation_tasks.get(cache_id) is completed:
                self._preparation_tasks.pop(cache_id, None)

        task.add_done_callback(discard)
        return task

    async def shutdown(self) -> None:
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cleanup_expired(self, *, keep_cache_id: str) -> None:
        cutoff = time.time() - self.retention.total_seconds()
        try:
            entries = list(self.audio_directory.glob("*.mp3"))
        except OSError as exc:
            logger.warning("audio cache cleanup could not list files: %s", exc)
            return
        for path in entries:
            if path.stem == keep_cache_id or not is_safe_cache_id(path.stem):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError as exc:
                logger.warning("audio cache cleanup failed for cache_id=%s: %s", path.stem, exc)

    async def _prepare_in_background(self, text: str) -> None:
        try:
            await self.get_or_create(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("background TTS generation failed: %s", exc)

    async def _generate(self, text: str, cache_id: str) -> AudioArtifact:
        provider = self.provider
        if provider is None:
            raise AudioUnavailableError("text-to-speech is not configured")
        final_path = self.audio_directory / f"{cache_id}.mp3"
        try:
            self.audio_directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=f".{cache_id[:12]}-",
                dir=self.audio_directory,
            ) as temporary_name:
                temporary_directory = Path(temporary_name)
                segments: list[Path] = []
                for index, chunk in enumerate(
                    chunk_spoken_text(text, max_characters=self.chunk_characters)
                ):
                    segment = temporary_directory / f"segment-{index:04d}.mp3"
                    await provider.synthesize(chunk, segment)
                    if not segment.is_file() or segment.stat().st_size == 0:
                        raise AudioGenerationError("TTS provider produced an empty segment")
                    segments.append(segment)
                staged_path = temporary_directory / "complete.mp3"
                await self.concatenator.concatenate(segments, staged_path)
                os.replace(staged_path, final_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with contextlib.suppress(FileNotFoundError):
                final_path.unlink()
            if isinstance(exc, AudioGenerationError):
                raise
            raise AudioGenerationError("audio generation failed") from exc

        await self.cleanup_expired(keep_cache_id=cache_id)
        return AudioArtifact(cache_id, final_path)


def chunk_spoken_text(text: str, *, max_characters: int = DEFAULT_CHUNK_CHARACTERS) -> list[str]:
    """Split deterministically at paragraph or Chinese sentence boundaries."""
    if max_characters < 1:
        raise ValueError("max_characters must be positive")
    normalized = text.strip()
    if not normalized:
        return []
    sentence_units = [
        match.group(0).strip()
        for match in re.finditer(r".+?(?:\n+|[。！？!?；;]+|$)", normalized, re.DOTALL)
        if match.group(0).strip()
    ]
    chunks: list[str] = []
    current = ""
    for unit in sentence_units:
        while len(unit) > max_characters:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:max_characters])
            unit = unit[max_characters:]
        if not unit:
            continue
        if current and len(current) + len(unit) > max_characters:
            chunks.append(current)
            current = unit
        else:
            current += unit
    if current:
        chunks.append(current)
    return chunks


def _is_nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0
