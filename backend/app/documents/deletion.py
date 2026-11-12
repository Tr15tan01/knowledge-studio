"""File deletion with derived-data cleanup and exactly-once storage release."""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId

from app.billing import usage
from app.billing.storage_accounting import release_file_storage
from app.core.errors import NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.jobs import queue
from app.jobs.runtime import JobContext, RetryableJobError, handler
from app.services import audit
from app.services.system_settings import get_quota_policy

log = logging.getLogger("app.deletion")


async def request_file_deletion(file_id: ObjectId, workspace_id: ObjectId, actor_id: ObjectId) -> ObjectId:
    db = get_db()
    f = await db.files.find_one_and_update(
        {"_id": file_id, "workspace_id": workspace_id, "storage_status": {"$in": ["stored", "rejected", "failed"]}},
        {"$set": {"storage_status": "deleting", "deletion_requested_at": utcnow(), "updated_at": utcnow()}},
    )
    if not f:
        existing = await db.files.find_one({"_id": file_id, "workspace_id": workspace_id}, projection={"storage_status": 1})
        if not existing:
            raise NotFound("File not found.")
        if existing["storage_status"] == "deleting":
            job = await db.jobs.find_one({"dedupe_key": f"delete:{file_id}"})
            return (
                job["_id"]
                if job
                else await queue.enqueue(
                    "file.delete", {"file_id": str(file_id)}, account_id=existing.get("owner_id"), dedupe_key=f"delete:{file_id}"
                )
            )
        raise ValidationFailed("This file can't be deleted right now (it may still be uploading).", code="not_deletable")
    if f.get("ingest_job_id"):
        await queue.request_cancel(f["ingest_job_id"])
    job_id = await queue.enqueue(
        "file.delete",
        {"file_id": str(file_id)},
        account_id=f["owner_id"],
        workspace_id=workspace_id,
        priority=5,
        dedupe_key=f"delete:{file_id}",
    )
    await audit.record("file.deletion_requested", actor_id=actor_id, target_type="file", target_id=file_id)
    return job_id


async def purge_derived_data(file_id: ObjectId, workspace_id: ObjectId) -> dict[str, int]:
    db = get_db()
    chunk_ids = [c["_id"] async for c in db.chunks.find({"file_id": file_id}, projection={"_id": 1})]
    affected = await db.entity_mentions.distinct("entity_id", {"file_id": file_id})
    m = await db.entity_mentions.delete_many({"file_id": file_id})
    # Relationship evidence pointing at this file's chunks is removed; edges without evidence go.
    await db.relationships.update_many(
        {"workspace_id": workspace_id, "evidence.file_id": file_id}, {"$pull": {"evidence": {"file_id": file_id}}}
    )
    r = await db.relationships.delete_many({"workspace_id": workspace_id, "evidence": {"$size": 0}})
    for eid in affected:
        n = await db.entity_mentions.count_documents({"entity_id": eid})
        if n == 0:
            await db.entities.delete_one({"_id": eid})
            await db.relationships.delete_many(
                {"workspace_id": workspace_id, "$or": [{"source_entity_id": eid}, {"target_entity_id": eid}]}
            )
        else:
            srcs = await db.entity_mentions.distinct("source_id", {"entity_id": eid})
            await db.entities.update_one({"_id": eid}, {"$set": {"mention_count": n, "source_ids": srcs}})
    c = await db.chunks.delete_many({"file_id": file_id})
    await db.books.delete_many({"file_id": file_id})
    await db.documents.delete_many({"file_id": file_id})
    return {"chunks": c.deleted_count, "mentions": m.deleted_count, "relationships": r.deleted_count, "chunk_ids": len(chunk_ids)}


@handler("file.delete")
async def delete_file(ctx: JobContext) -> dict[str, Any]:
    db = get_db()
    file_id = ObjectId(ctx.payload["file_id"])
    f = await db.files.find_one({"_id": file_id})
    if not f:
        return {"skipped": "missing"}
    if f["storage_status"] == "deleted":
        return {"skipped": "already_deleted"}
    if f["storage_status"] != "deleting":
        return {"skipped": f"status_{f['storage_status']}"}
    stats = await purge_derived_data(file_id, f["workspace_id"])
    from app.storage import storage_for

    try:
        gone = await storage_for(f).delete(f["storage_key"])
    except Exception as exc:  # noqa: BLE001
        raise RetryableJobError("storage_delete_failed", "Object storage deletion failed; will retry.") from exc
    if not gone:
        raise RetryableJobError("storage_delete_unconfirmed", "Storage deletion could not be confirmed; will retry.")
    released = await release_file_storage(f)
    policy = await get_quota_policy()
    restored = False
    if policy.upload_quota_restore_on_delete and f.get("upload_period_key") and f.get("upload_reservation_id"):
        from app.billing.entitlements import resolve

        r = await resolve(f["owner_id"])
        if r.period.key == f["upload_period_key"]:
            restored = await usage.record_usage(
                account_id=f["owner_id"],
                metric="uploads",
                amount=-1,
                period=r.period,
                idempotency_key=f"upload_restore:{file_id}",
                meta={"file_id": str(file_id)},
            )
    return {**stats, "storage_released": released, "upload_restored": restored}
