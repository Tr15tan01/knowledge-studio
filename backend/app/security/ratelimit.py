"""MongoDB-backed fixed-window rate limiter.

Shared across API instances (unlike in-memory limiters). Windows expire via a TTL index.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument

from app.core.config import get_settings
from app.core.errors import RateLimited
from app.database.client import get_db


def _key(scope: str, identifier: str, window_start: int) -> str:
    ident = hashlib.sha256(identifier.encode()).hexdigest()[:32]  # avoid storing raw emails/IPs
    return f"{scope}:{ident}:{window_start}"


async def hit(scope: str, identifier: str, *, limit: int, window_seconds: int) -> int:
    """Count a hit and raise RateLimited when the limit is exceeded."""
    if not get_settings().rate_limit_enabled or limit <= 0:
        return 0
    now = int(time.time())
    window_start = now - (now % window_seconds)
    key = _key(scope, identifier, window_start)
    doc = await get_db().rate_limits.find_one_and_update(
        {"_id": key},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {"expires_at": datetime.fromtimestamp(window_start + window_seconds, UTC) + timedelta(seconds=5)},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    count = int(doc["count"]) if doc else 1
    if count > limit:
        raise RateLimited(
            "Too many requests. Please wait and try again.",
            details={"retry_after": window_start + window_seconds - now, "scope": scope},
        )
    return count
