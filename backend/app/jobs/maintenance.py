"""Scheduled maintenance: reservation expiry, storage reconciliation, stale-upload and original-file cleanup."""

from __future__ import annotations

from typing import Any

from app.billing.storage_accounting import reconcile_storage_objects
from app.billing.usage import expire_stale_reservations
from app.documents.originals import purge_due_originals
from app.jobs.runtime import JobContext, handler


@handler("maintenance.reconcile")
async def reconcile(ctx: JobContext) -> dict[str, Any]:
    released = await expire_stale_reservations()
    await ctx.checkpoint()
    report = await reconcile_storage_objects()
    await ctx.checkpoint()
    originals = await purge_due_originals()
    exports = await cleanup_exports(ctx)
    return {
        "reservations_released": released,
        **report,
        "originals_purged": originals,
        "expired_exports_deleted": (exports or {}).get("deleted", 0),
    }


@handler("maintenance.cleanup_exports")
async def cleanup_exports(ctx: JobContext) -> dict[str, Any]:
    from app.core.timeutil import utcnow
    from app.database.client import get_db
    from app.storage import storage_for

    n = 0
    async for x in get_db().data_exports.find({"expires_at": {"$lt": utcnow()}}).limit(500):
        if await storage_for(x).delete(x["storage_key"]):
            await get_db().data_exports.delete_one({"_id": x["_id"]})
            n += 1
    return {"deleted": n}
