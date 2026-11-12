"""Account deletion and data export jobs."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import timedelta
from typing import Any

from bson import ObjectId

from app.core.timeutil import utcnow
from app.database.client import get_db
from app.documents.deletion import purge_derived_data
from app.jobs.runtime import JobContext, RetryableJobError, handler
from app.services import audit

EXPORT_INLINE_FILE_LIMIT = 1024 * 1024 * 1024  # include originals up to 1 GiB total


@handler("account.delete")
async def delete_account(ctx: JobContext) -> dict[str, Any]:
    """Delete all user content and derived data; keep a pseudonymised stub for billing/audit integrity."""
    from app.storage import storage_for

    db = get_db()
    uid = ObjectId(ctx.payload["user_id"])
    user = await db.users.find_one({"_id": uid})
    if not user:
        return {"skipped": "missing"}
    workspaces = [w["_id"] async for w in db.workspaces.find({"owner_id": uid}, projection={"_id": 1})]
    counts: dict[str, int] = {"files": 0}
    for ws in workspaces:
        async for f in db.files.find({"workspace_id": ws, "storage_status": {"$ne": "deleted"}}):
            await ctx.checkpoint()
            await purge_derived_data(f["_id"], ws)
            try:
                if not await storage_for(f).delete(f["storage_key"]):
                    raise RetryableJobError("storage_delete_unconfirmed", "Storage deletion could not be confirmed.")
            except RetryableJobError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RetryableJobError("storage_delete_failed", "Storage deletion failed; will retry.") from exc
            await db.files.update_one(
                {"_id": f["_id"]},
                {
                    "$set": {
                        "storage_status": "deleted",
                        "deleted_at": utcnow(),
                        "original_filename": "[deleted]",
                        "display_title": "[deleted]",
                    }
                },
            )
            counts["files"] += 1
        for coll in (
            "chunks",
            "entities",
            "relationships",
            "entity_mentions",
            "conversations",
            "research_projects",
            "research_findings",
            "reports",
            "collections",
            "saved_searches",
            "books",
            "documents",
        ):
            res = await db[coll].delete_many({"workspace_id": ws})
            counts[coll] = counts.get(coll, 0) + res.deleted_count
        await db.messages.delete_many({"workspace_id": ws})
        await db.research_jobs.delete_many({"workspace_id": ws})
        await db.workspace_memberships.delete_many({"workspace_id": ws})
        await db.workspaces.delete_one({"_id": ws})
    for coll in ("sessions", "oauth_accounts", "email_verification_tokens", "password_reset_tokens"):
        await db[coll].delete_many({"user_id": uid})
    pseudo = hashlib.sha256(f"deleted:{uid}".encode()).hexdigest()[:16]
    await db.users.update_one(
        {"_id": uid},
        {
            "$set": {
                "email": f"deleted-{pseudo}@invalid",
                "email_normalized": f"deleted-{pseudo}@invalid",
                "display_name": "Deleted user",
                "password_hash": None,
                "status": "deleting",
                "deleted_at": utcnow(),
                "preferences": {},
            }
        },
    )
    await audit.record(
        "account.deleted", actor_type="system", target_type="user", target_id=uid, details={k: v for k, v in counts.items() if v}
    )
    return counts


@handler("account.export")
async def export_account(ctx: JobContext) -> dict[str, Any]:
    from app.storage import active_storage, storage_for

    db = get_db()
    uid = ObjectId(ctx.payload["user_id"])
    user = await db.users.find_one({"_id": uid})
    if not user:
        return {"skipped": "missing"}
    workspaces = [w["_id"] async for w in db.workspaces.find({"owner_id": uid}, projection={"_id": 1})]
    buf = io.BytesIO()

    def clean(d: dict[str, Any]) -> dict[str, Any]:
        drop = {"password_hash", "token_hash", "csrf_hash", "embedding", "storage_key"}
        return {k: v for k, v in d.items() if k not in drop and not k.startswith("embedding")}

    included_bytes = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("account.json", json.dumps(clean(user), default=str, indent=2))
        for ws in workspaces:
            for coll in (
                "files",
                "books",
                "documents",
                "collections",
                "conversations",
                "messages",
                "research_projects",
                "research_findings",
                "reports",
            ):
                rows = [clean(r) async for r in db[coll].find({"workspace_id": ws}).limit(100_000)]
                z.writestr(f"{ws}/{coll}.json", json.dumps(rows, default=str, indent=2))
            async for rep in db.reports.find({"workspace_id": ws}):
                z.writestr(f"{ws}/reports/{rep['_id']}.md", rep.get("markdown", ""))
            # Originals are included only where the server still keeps them.
            async for f in db.files.find({"workspace_id": ws, "storage_status": "stored", "original_purged_at": None}):
                await ctx.checkpoint()
                size = int(f.get("original_size_bytes") or 0)
                if included_bytes + size > EXPORT_INLINE_FILE_LIMIT:
                    continue
                data = await storage_for(f).read_bytes(f["storage_key"], max_bytes=size + 1)
                z.writestr(f"{ws}/originals/{f['_id']}-{f['original_filename']}", data)
                included_bytes += size
    key = f"exports/{uid}/{ObjectId()}.zip"

    async def gen():  # type: ignore[no-untyped-def]
        yield buf.getvalue()

    storage = await active_storage()
    stored = await storage.put_stream(key, gen(), max_bytes=4 * 1024 * 1024 * 1024, content_type="application/zip")
    await db.data_exports.insert_one(
        {
            "user_id": uid,
            "storage_key": key,
            "storage_backend": storage.name,
            "size_bytes": stored.size_bytes,
            "created_at": utcnow(),
            "expires_at": utcnow() + timedelta(days=7),
            "job_id": ctx.id,
        }
    )
    await audit.record(
        "account.export_created", actor_type="system", target_type="user", target_id=uid, details={"bytes": stored.size_bytes}
    )
    return {"size_bytes": stored.size_bytes}
