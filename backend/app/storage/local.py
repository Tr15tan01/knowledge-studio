"""Local filesystem storage for development. Files live outside any web root."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

from app.storage.base import ObjectStorage, StoredObject, UploadTooLarge

_SAFE = set("abcdefghijklmnopqrstuvwxyz0123456789/-_.")


class LocalStorage(ObjectStorage):
    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are generated internally, but defend against traversal regardless.
        if not key or any(c not in _SAFE for c in key) or ".." in key or key.startswith("/"):
            raise ValueError("invalid storage key")
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int, content_type: str) -> StoredObject:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        h = hashlib.sha256()
        size = 0
        head = b""
        f = await asyncio.to_thread(open, tmp, "wb")
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge(max_bytes)
                if len(head) < 8192:
                    head += chunk[: 8192 - len(head)]
                h.update(chunk)
                await asyncio.to_thread(f.write, chunk)
            await asyncio.to_thread(f.flush)
            await asyncio.to_thread(os.fsync, f.fileno())
        except BaseException:
            await asyncio.to_thread(f.close)
            await asyncio.to_thread(tmp.unlink, True)
            raise
        await asyncio.to_thread(f.close)
        await asyncio.to_thread(os.replace, tmp, path)
        return StoredObject(key=key, size_bytes=size, sha256=h.hexdigest(), head=head)

    async def open_read(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        f = await asyncio.to_thread(open, path, "rb")
        try:
            while True:
                b = await asyncio.to_thread(f.read, 256 * 1024)
                if not b:
                    break
                yield b
        finally:
            await asyncio.to_thread(f.close)

    async def read_bytes(self, key: str, *, max_bytes: int) -> bytes:
        path = self._path(key)
        size = (await asyncio.to_thread(path.stat)).st_size
        if size > max_bytes:
            raise UploadTooLarge(max_bytes)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)
        return not path.exists()

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def size(self, key: str) -> int | None:
        p = self._path(key)
        return p.stat().st_size if p.exists() else None

    async def list_keys(self, prefix: str = "") -> AsyncIterator[tuple[str, int, float]]:
        base = self.root
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if fn.endswith(".part"):
                    continue
                full = Path(dirpath) / fn
                key = full.relative_to(base).as_posix()
                if key.startswith(prefix):
                    st = full.stat()
                    yield key, st.st_size, st.st_mtime

    async def health(self) -> tuple[bool, str]:
        try:
            probe = self.root / ".health"
            await asyncio.to_thread(probe.write_bytes, b"ok")
            await asyncio.to_thread(probe.unlink, True)
            return True, "writable"
        except OSError as exc:
            return False, f"not writable: {type(exc).__name__}"
