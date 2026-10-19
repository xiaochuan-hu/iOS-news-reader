from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol


class TTSProvider(Protocol):
    """Synthesizes one conservatively sized text chunk into an MP3 segment."""

    @property
    def name(self) -> str: ...

    @property
    def cache_identity(self) -> Mapping[str, str]: ...

    async def synthesize(self, text: str, output_path: Path) -> None: ...


class AudioConcatenator(Protocol):
    async def concatenate(self, segments: list[Path], output_path: Path) -> None: ...
