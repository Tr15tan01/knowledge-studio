"""MongoDB (GridFS) storage: files live in the application database, so the API and the
workers can run on different servers without a shared disk or an object store."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from typing import Any

from gridfs import AsyncGridFSBucket
from gridfs.errors import NoFile

from app.database.client import get_db
from app.storage.base import ObjectStorage, StoredObject, UploadTooLarge

BUCKET = "storage"
READ_SIZE = 256 * 1024


class MongoStorage(ObjectStorage):
    name = "mongodb"

    def _bucket(self) -> AsyncGridFSBucket:
        # Resolved per call: tests switch databases on the shared client.
        return AsyncGridFSBucket(get_db(), bucket_name=BUCKET)

    def _files(self) -> Any:
        return get_db()[f"{BUCKET}.files"]

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int, content_type: str) -> StoredObject:
        await self.delete(key)  # a key names one object, as in the other backends
        h = hashlib.sha256()
        size = 0
        head = b""
        grid_in = self._bucket().open_upload_stream(key, metadata={"content_type": content_type})
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
                await grid_in.write(chunk)
        except BaseException:
            await grid_in.abort()
            raise
        await grid_in.close()
        return StoredObject(key=key, size_bytes=size, sha256=h.hexdigest(), head=head)

    async def open_read(self, key: str) -> AsyncIterator[bytes]:
        try:
            out = await self._bucket().open_download_stream_by_name(key)
        except NoFile as exc:
            raise FileNotFoundError(key) from exc
        while True:
            b = await out.read(READ_SIZE)
            if not b:
                break
            yield b

    async def read_bytes(self, key: str, *, max_bytes: int) -> bytes:
        try:
            out = await self._bucket().open_download_stream_by_name(key)
        except NoFile as exc:
            raise FileNotFoundError(key) from exc
        if out.length > max_bytes:
            raise UploadTooLarge(max_bytes)
        return await out.read()

    async def delete(self, key: str) -> bool:
        bucket = self._bucket()
        async for f in self._files().find({"filename": key}, projection={"_id": 1}):
            try:
                await bucket.delete(f["_id"])
            except NoFile:  # removed concurrently
                pass
        return not await self.exists(key)

    async def exists(self, key: str) -> bool:
        return await self._files().find_one({"filename": key}, projection={"_id": 1}) is not None

    async def size(self, key: str) -> int | None:
        f = await self._files().find_one({"filename": key}, projection={"length": 1}, sort=[("uploadDate", -1)])
        return int(f["length"]) if f else None

    async def list_keys(self, prefix: str = "") -> AsyncIterator[tuple[str, int, float]]:
        flt = {"filename": {"$regex": f"^{re.escape(prefix)}"}} if prefix else {}
        async for f in self._files().find(flt, projection={"filename": 1, "length": 1, "uploadDate": 1}):
            yield f["filename"], int(f["length"]), f["uploadDate"].timestamp()

    async def health(self) -> tuple[bool, str]:
        try:
            await get_db().command("ping")
            return True, "database reachable"
        except Exception as exc:  # noqa: BLE001
            return False, f"unreachable: {type(exc).__name__}"
