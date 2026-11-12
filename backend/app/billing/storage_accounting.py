"""Byte-accurate storage accounting for uploaded original files.

* Integer bytes are the source of truth; binary units (MiB/GiB) are display-only.
* The user-facing quota measures *original uploaded files* only. Extracted text, chunks,
  embeddings, graph data and reports are tracked separately as internal metrics.
* The quota is the size of the library: a file counts with its original size until the
  user deletes it, whether or not the server still keeps the original after indexing.
* The storage unit is the ``files`` record. Books and documents reference a file by id, so
  a file is never counted twice.
* Quota counter: ``usage_counters`` metric ``storage_bytes`` period ``lifetime``.
  Uploads reserve bytes atomically before streaming; finalisation commits the verified
  size; deletion releases it once, after the object is confirmed deleted.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from bson import ObjectId

from app.billing import usage
from app.billing.entitlements import Resolved
from app.billing.periods import LIFETIME
from app.core.timeutil import utcnow
from app.core.units import format_bytes
from app.database.client import get_db

log = logging.getLogger("app.billing.storage")

COUNTED_STATES = ("stored", "deleting")  # bytes occupy storage until deletion is confirmed


async def storage_breakdown(account_id: Any) -> dict[str, Any]:
    db = get_db()
    out: dict[str, Any] = {
        "stored_bytes": 0,
        "book_bytes": 0,
        "document_bytes": 0,
        "stored_files": 0,
        "uploading_files": 0,
        "uploading_bytes": 0,
        "deleting_files": 0,
        "failed_uploads": 0,
    }
    async for row in await db.files.aggregate(
        [
            {"$match": {"owner_id": account_id, "storage_status": {"$in": ["uploading", "stored", "deleting", "failed", "rejected"]}}},
            {
                "$group": {
                    "_id": {"s": "$storage_status", "k": "$kind"},
                    "n": {"$sum": 1},
                    "b": {"$sum": {"$ifNull": ["$original_size_bytes", 0]}},
                }
            },
        ]
    ):
        s, k, n, b = row["_id"]["s"], row["_id"].get("k"), row["n"], int(row["b"])
        if s in COUNTED_STATES:
            out["stored_bytes"] += b
            out["stored_files"] += n
            if k == "book":
                out["book_bytes"] += b
            else:
                out["document_bytes"] += b
            if s == "deleting":
                out["deleting_files"] += n
        elif s == "uploading":
            out["uploading_files"] += n
        else:
            out["failed_uploads"] += n
    return out


async def storage_summary(r: Resolved, thresholds: tuple[int, ...] = (80, 90, 100)) -> dict[str, Any]:
    snap = await usage.usage_snapshot(r.account_id, "storage_bytes", LIFETIME)
    breakdown = await storage_breakdown(r.account_id)
    limit = int(r.limit("storage_bytes") or 0)
    used = snap["used"]
    pct = (used / limit * 100) if limit else 0.0
    level = None
    for t in sorted(thresholds):
        if pct >= t:
            level = t
    return {
        "used_bytes": used,
        "reserved_bytes": snap["reserved"],
        "limit_bytes": limit,
        "available_bytes": max(0, limit - used - snap["reserved"]),
        "max_file_size_bytes": r.entitlements.max_file_size_bytes,
        "percent_used": round(pct, 2),
        "warning_level": level,
        "over_limit": used > limit,
        "display": {
            "used": format_bytes(used),
            "limit": format_bytes(limit),
            "available": format_bytes(max(0, limit - used)),
            "max_file_size": format_bytes(r.entitlements.max_file_size_bytes),
        },
        "breakdown": breakdown,
        "unit_system": "binary (1 MiB = 1,048,576 bytes)",
    }


async def release_file_storage(file_doc: dict[str, Any]) -> bool:
    """Release a deleted file's bytes exactly once.

    Call only after the storage object deletion is confirmed. The ledger entry uses a
    per-file idempotency key, so retries (or concurrent deletion workers) can never
    double-release; the state transition afterwards is guarded on ``deleting``.
    """
    db = get_db()
    f = await db.files.find_one({"_id": file_doc["_id"]})
    if not f or f["storage_status"] != "deleting":
        return False
    released = False
    if f.get("storage_committed"):
        released = await usage.record_usage(
            account_id=f["owner_id"],
            metric="storage_bytes",
            amount=-int(f.get("original_size_bytes") or 0),
            period=LIFETIME,
            idempotency_key=f"storage_release:{f['_id']}",
            meta={"file_id": str(f["_id"])},
        )
    await db.files.update_one(
        {"_id": f["_id"], "storage_status": "deleting"},
        {"$set": {"storage_status": "deleted", "deleted_at": utcnow(), "quota_released_at": utcnow(), "updated_at": utcnow()}},
    )
    return released


async def _storage_quiescent(account_id: Any) -> bool:
    db = get_db()
    if await db.files.count_documents({"owner_id": account_id, "storage_status": {"$in": ["uploading", "deleting"]}}, limit=1):
        return False
    cid = usage.counter_id(account_id, "storage_bytes", "lifetime")
    return not await db.usage_reservations.count_documents({"counter_id": cid, "state": {"$in": ["applying", "pending"]}}, limit=1)


async def reconcile_account_storage(account_id: Any) -> dict[str, Any]:
    """Recompute storage usage from file records and repair the counter + ledger safely.

    Runs only while the account has no in-flight uploads or deletions (checked before and
    after the computation, together with the counter version) so it cannot race with the
    upload commit or deletion release paths.
    """
    db = get_db()
    cid = usage.counter_id(account_id, "storage_bytes", "lifetime")
    if not await _storage_quiescent(account_id):
        return {"account_id": str(account_id), "skipped": "activity_in_progress"}
    before = await db.usage_counters.find_one({"_id": cid}, projection={"version": 1})
    await usage.repair_committed_events(cid)  # ledger must include every committed reservation first
    truth = 0
    async for row in await db.files.aggregate(
        [
            {"$match": {"owner_id": account_id, "storage_status": {"$in": list(COUNTED_STATES)}, "storage_committed": True}},
            {"$group": {"_id": None, "b": {"$sum": "$original_size_bytes"}}},
        ]
    ):
        truth = int(row["b"])
    ledger = 0
    async for row in await db.usage_events.aggregate(
        [
            {"$match": {"account_id": account_id, "metric": "storage_bytes", "period_key": "lifetime"}},
            {"$group": {"_id": None, "s": {"$sum": "$amount"}}},
        ]
    ):
        ledger = int(row["s"])
    delta = truth - ledger
    after = await db.usage_counters.find_one({"_id": cid}, projection={"version": 1})
    if not await _storage_quiescent(account_id) or (before or {}).get("version") != (after or {}).get("version"):
        return {"account_id": str(account_id), "skipped": "activity_in_progress"}
    if delta:
        await usage.record_usage(
            account_id=account_id,
            metric="storage_bytes",
            amount=delta,
            period=LIFETIME,
            idempotency_key=f"storage_reconcile:{account_id}:{utcnow().isoformat()}",
            meta={"reason": "reconciliation", "truth": truth, "ledger": ledger},
        )
        log.warning("storage_ledger_repaired", extra={"account": str(account_id), "truth": truth, "ledger": ledger})
        await db.reconciliation_reports.insert_one(
            {"kind": "storage_ledger", "account_id": account_id, "truth": truth, "ledger": ledger, "delta": delta, "created_at": utcnow()}
        )
    counter = await usage.reconcile_counter(cid)
    return {
        "account_id": str(account_id),
        "truth_bytes": truth,
        "ledger_bytes_before": ledger,
        "repaired": bool(delta),
        "counter": bool(counter),
    }


async def reconcile_storage_objects(
    *, orphan_grace: timedelta = timedelta(hours=24), stale_upload: timedelta = timedelta(hours=1)
) -> dict[str, Any]:
    """Compare file records with storage objects.

    * Uploads stuck in ``uploading`` are failed and their reservations released.
    * Objects with no file record older than the grace period are deleted (orphans).
    * ``stored`` files whose object is missing are flagged (never silently deleted).
      Originals deleted on purpose after indexing are not missing.
    """
    from app.storage import available_backends, storage_backend, storage_for

    db = get_db()
    now = utcnow()
    report: dict[str, Any] = {"stale_uploads": 0, "orphans_deleted": 0, "missing_objects": 0, "accounts_reconciled": 0}

    async for f in db.files.find({"storage_status": "uploading", "created_at": {"$lt": now - stale_upload}}).limit(1000):
        res = await db.files.update_one(
            {"_id": f["_id"], "storage_status": "uploading"},
            {"$set": {"storage_status": "failed", "failure_reason": "upload_abandoned", "updated_at": now}},
        )
        if res.modified_count:
            await storage_for(f).delete(f["storage_key"])
            for rid in (f.get("upload_reservation_id"), f.get("storage_reservation_id")):
                if rid:
                    await usage.release(rid, reason="upload_abandoned")
            report["stale_uploads"] += 1

    known: set[str] = set()
    async for f in db.files.find(
        {"storage_status": {"$in": ["uploading", "stored", "deleting"]}}, projection={"storage_key": 1, "storage_status": 1}
    ):
        known.add(f["storage_key"])
    for name in available_backends():
        storage = storage_backend(name)
        async for key, _size, mtime in storage.list_keys("files/"):
            if key in known:
                continue
            if now.timestamp() - mtime > orphan_grace.total_seconds():
                await storage.delete(key)
                report["orphans_deleted"] += 1
                log.warning("orphan_object_deleted", extra={"key_prefix": key[:24]})

    async for f in db.files.find(
        {"storage_status": "stored", "storage_missing": {"$ne": True}, "original_purged_at": None},
        projection={"storage_key": 1, "storage_backend": 1},
    ).limit(20000):
        if not await storage_for(f).exists(f["storage_key"]):
            await db.files.update_one({"_id": f["_id"]}, {"$set": {"storage_missing": True, "updated_at": now}})
            report["missing_objects"] += 1
            log.error("stored_object_missing", extra={"file": str(f["_id"])})

    accounts = await db.files.distinct("owner_id")
    for acct in accounts:
        await reconcile_account_storage(acct)
        report["accounts_reconciled"] += 1
    await db.reconciliation_reports.insert_one({"kind": "storage_objects", **report, "created_at": now})
    return report


def new_storage_key(workspace_id: ObjectId) -> str:
    import secrets

    return f"files/{str(workspace_id)}/{secrets.token_hex(16)}"
