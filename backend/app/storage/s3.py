"""S3-compatible storage (AWS S3, Cloudflare R2, Backblaze B2, MinIO, ...).

boto3 is synchronous; calls run in worker threads. Uploads stream through a multipart
upload with a hard byte limit enforced server-side as bytes arrive.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import Settings
from app.storage.base import ObjectStorage, StoredObject, UploadTooLarge

PART_SIZE = 8 * 1024 * 1024


class S3Storage(ObjectStorage):
    name = "s3"

    def __init__(self, s: Settings) -> None:
        import boto3  # local import keeps dev startup light
        from botocore.config import Config

        self.bucket = s.s3_bucket
        self._client: Any = boto3.client(
            "s3",
            region_name=s.s3_region or None,
            endpoint_url=s.s3_endpoint_url or None,
            aws_access_key_id=s.s3_access_key_id.get_secret_value() or None,
            aws_secret_access_key=s.s3_secret_access_key.get_secret_value() or None,
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=10, read_timeout=60),
        )

    async def _call(self, fn: str, **kw: Any) -> Any:
        return await asyncio.to_thread(getattr(self._client, fn), **kw)

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes], *, max_bytes: int, content_type: str) -> StoredObject:
        mpu = await self._call(
            "create_multipart_upload", Bucket=self.bucket, Key=key, ContentType="application/octet-stream", ServerSideEncryption="AES256"
        )
        upload_id = mpu["UploadId"]
        parts: list[dict[str, Any]] = []
        buf = bytearray()
        h = hashlib.sha256()
        size = 0
        head = b""
        try:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge(max_bytes)
                if len(head) < 8192:
                    head += chunk[: 8192 - len(head)]
                h.update(chunk)
                buf.extend(chunk)
                if len(buf) >= PART_SIZE:
                    n = len(parts) + 1
                    r = await self._call("upload_part", Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=n, Body=bytes(buf))
                    parts.append({"ETag": r["ETag"], "PartNumber": n})
                    buf.clear()
            n = len(parts) + 1
            r = await self._call("upload_part", Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=n, Body=bytes(buf))
            parts.append({"ETag": r["ETag"], "PartNumber": n})
            await self._call("complete_multipart_upload", Bucket=self.bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts})
        except BaseException:
            await self._call("abort_multipart_upload", Bucket=self.bucket, Key=key, UploadId=upload_id)
            raise
        # Server-side verification of the final size (never trust the client).
        real = await self.size(key)
        if real != size:
            await self.delete(key)
            raise OSError("stored size mismatch")
        return StoredObject(key=key, size_bytes=size, sha256=h.hexdigest(), head=head)

    async def open_read(self, key: str) -> AsyncIterator[bytes]:
        obj = await self._call("get_object", Bucket=self.bucket, Key=key)
        body = obj["Body"]
        try:
            while True:
                b = await asyncio.to_thread(body.read, 256 * 1024)
                if not b:
                    break
                yield b
        finally:
            body.close()

    async def read_bytes(self, key: str, *, max_bytes: int) -> bytes:
        sz = await self.size(key)
        if sz is None:
            raise FileNotFoundError(key)
        if sz > max_bytes:
            raise UploadTooLarge(max_bytes)
        out = bytearray()
        async for b in self.open_read(key):
            out.extend(b)
        return bytes(out)

    async def delete(self, key: str) -> bool:
        await self._call("delete_object", Bucket=self.bucket, Key=key)
        return not await self.exists(key)

    async def exists(self, key: str) -> bool:
        return await self.size(key) is not None

    async def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            r = await self._call("head_object", Bucket=self.bucket, Key=key)
            return int(r["ContentLength"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    async def list_keys(self, prefix: str = "") -> AsyncIterator[tuple[str, int, float]]:
        token = None
        while True:
            kw: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            r = await self._call("list_objects_v2", **kw)
            for o in r.get("Contents", []):
                yield o["Key"], int(o["Size"]), o["LastModified"].timestamp()
            if not r.get("IsTruncated"):
                break
            token = r.get("NextContinuationToken")

    async def signed_download_url(self, key: str, *, filename: str, ttl_seconds: int) -> str | None:
        safe = "".join(c for c in filename if c.isalnum() or c in " ._-")[:120] or "download"
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": f'attachment; filename="{safe}"'},
            ExpiresIn=ttl_seconds,
        )

    async def health(self) -> tuple[bool, str]:
        try:
            await self._call("head_bucket", Bucket=self.bucket)
            return True, "bucket reachable"
        except Exception as exc:  # noqa: BLE001
            return False, f"unreachable: {type(exc).__name__}"
