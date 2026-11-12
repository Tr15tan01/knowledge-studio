"""File storage backends.

New files go to the backend an administrator selected (``active_storage``). Every record
remembers the backend it was written to, so existing files stay readable and deletable
after the selection changes (``storage_for``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.storage.base import ObjectStorage


@lru_cache
def storage_backend(name: str) -> ObjectStorage:
    s = get_settings()
    if name == "s3":
        from app.storage.s3 import S3Storage

        return S3Storage(s)
    if name == "mongodb":
        from app.storage.mongo import MongoStorage

        return MongoStorage()
    if name == "local":
        from app.storage.local import LocalStorage

        return LocalStorage(s.local_storage_dir)
    raise ValueError(f"unknown storage backend: {name}")


def available_backends() -> list[str]:
    """Backends an administrator can select. S3 is optional and appears only when configured."""
    return ["local", "mongodb", *(["s3"] if get_settings().s3_bucket else [])]


async def active_storage() -> ObjectStorage:
    from app.services.system_settings import get_runtime_settings

    return storage_backend((await get_runtime_settings()).storage_backend)


def storage_for(record: dict[str, Any]) -> ObjectStorage:
    """The backend holding a file or export record's object."""
    return storage_backend(record.get("storage_backend") or get_settings().storage_backend)
