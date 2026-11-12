"""Original uploaded files after indexing.

Answers, citations, research and the graph are served from the passages stored in the
database, so the original file is only needed until indexing succeeds. Unless an
administrator turns on "keep original files", it is deleted then. The library entry, its
checksum (duplicate detection) and its quota bytes stay until the user deletes the file.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.services.system_settings import get_runtime_settings
from app.storage import storage_for

log = logging.getLogger("app.originals")


def original_available(f: dict[str, Any]) -> bool:
    return f.get("storage_status") == "stored" and not f.get("original_purged_at")


async def purge_original(f: dict[str, Any]) -> bool:
    """Delete a file's stored original. Idempotent; a failed delete stays pending for maintenance."""
    db = get_db()
    try:
        gone = await storage_for(f).delete(f["storage_key"])
    except Exception as exc:  # noqa: BLE001 - never fail indexing over cleanup
        log.warning("original_purge_failed", extra={"file": str(f["_id"]), "error": type(exc).__name__})
        gone = False
    if not gone:
        await db.files.update_one({"_id": f["_id"]}, {"$set": {"original_purge_pending": True}})
        return False
    now = utcnow()
    await db.files.update_one({"_id": f["_id"]}, {"$set": {"original_purged_at": now, "original_purge_pending": False, "updated_at": now}})
    return True


async def purge_due_originals(limit: int = 500) -> int:
    """Maintenance sweep: retry pending purges, and drop originals of files that failed
    processing longer ago than the retention window (retrying them needs a new upload)."""
    db = get_db()
    n = 0
    async for f in db.files.find({"storage_status": "stored", "original_purge_pending": True, "original_purged_at": None}).limit(limit):
        n += await purge_original(f)
    if (await get_runtime_settings()).keep_original_files:
        return n
    cutoff = utcnow() - timedelta(days=get_settings().failed_original_retention_days)
    async for f in db.files.find(
        {"storage_status": "stored", "processing_status": "failed", "original_purged_at": None, "updated_at": {"$lt": cutoff}}
    ).limit(limit):
        n += await purge_original(f)
    return n
