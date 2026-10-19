from __future__ import annotations

import hashlib
import hmac
import re
import time

CACHE_ID_PATTERN = re.compile(r"[0-9a-f]{64}")


def is_safe_cache_id(cache_id: str) -> bool:
    return CACHE_ID_PATTERN.fullmatch(cache_id) is not None


class MediaURLSigner:
    def __init__(self, key: str) -> None:
        self._key = key.encode("utf-8")

    @property
    def available(self) -> bool:
        return bool(self._key)

    def sign(self, cache_id: str, expires: int) -> str:
        if not self.available:
            raise RuntimeError("media signing is not configured")
        if not is_safe_cache_id(cache_id):
            raise ValueError("invalid audio cache id")
        return hmac.new(
            self._key,
            self._payload(cache_id, expires),
            hashlib.sha256,
        ).hexdigest()

    def verify(
        self,
        cache_id: str,
        expires: int,
        signature: str,
        *,
        now: int | None = None,
    ) -> bool:
        if not self.available or not is_safe_cache_id(cache_id):
            return False
        if expires <= (int(time.time()) if now is None else now):
            return False
        expected = self.sign(cache_id, expires)
        return hmac.compare_digest(signature, expected)

    @staticmethod
    def _payload(cache_id: str, expires: int) -> bytes:
        return f"morning-audio-v1:{cache_id}:{expires}".encode()
