"""Object storage abstraction."""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass


class UploadTooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Upload exceeded {limit} bytes")
        self.limit = limit


@dataclass
class StoredObject:
    key: str
    size_bytes: int
    sha256: str
    head: bytes  # first bytes, for signature sniffing


class ObjectStorage(abc.ABC):
    name: str

    @abc.abstractmethod
    async def put_stream(self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int, content_type: str) -> StoredObject:
        """Write a stream, aborting (and cleaning up) once ``max_bytes`` is exceeded."""

    @abc.abstractmethod
    def open_read(self, key: str) -> AsyncIterator[bytes]:
        """Stream the object's bytes (implemented as an async generator)."""

    @abc.abstractmethod
    async def read_bytes(self, key: str, *, max_bytes: int) -> bytes: ...

    @abc.abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete an object. Returns True if it is gone afterwards (idempotent)."""

    @abc.abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    async def size(self, key: str) -> int | None: ...

    @abc.abstractmethod
    def list_keys(self, prefix: str = "") -> AsyncIterator[tuple[str, int, float]]:
        """Yield (key, size, mtime_epoch)."""

    async def signed_download_url(self, key: str, *, filename: str, ttl_seconds: int) -> str | None:
        """Return a short-lived URL, or None if the backend streams through the API instead."""
        return None

    @abc.abstractmethod
    async def health(self) -> tuple[bool, str]: ...
