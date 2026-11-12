"""Upload acceptance: entitlement checks, atomic reservations, streamed storage, validation.

Policy summary (docs/billing-and-plans.md):
* An upload consumes one monthly upload allowance only after the server has stored the
  file and validated its type, size and signature. Rejected or failed uploads release
  their reservations and never consume the allowance.
* Storage bytes are reserved before streaming (from the mandatory Content-Length), the
  stream is cut off at that size and at the plan's per-file limit, and the verified size
  is committed.
* Exact duplicates (same SHA-256) in a workspace are rejected and release their holds.
* Replacing a file is a new upload (upload the new version, delete the old one).
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import AsyncIterator
from typing import Any

from bson import ObjectId
from fastapi import Request

from app.auth.deps import WorkspaceContext
from app.billing import usage
from app.billing.entitlements import Resolved, reserve_metric, resolve
from app.billing.storage_accounting import new_storage_key
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, ValidationFailed
from app.core.timeutil import utcnow
from app.core.units import format_bytes
from app.database.client import get_db
from app.documents.detect import SUPPORTED_EXTENSIONS, DetectionError, detect_from_head, extension_of, validate_docx_bytes
from app.jobs import queue
from app.security import ratelimit
from app.services import audit
from app.storage import active_storage
from app.storage.base import UploadTooLarge

log = logging.getLogger("app.upload")


class FileTooLarge(AppError):
    status_code = 413
    code = "file_too_large"


def safe_display_name(raw: str) -> str:
    name = urllib.parse.unquote(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip()
    return name[:200] or "untitled"


async def _stream(request: Request) -> AsyncIterator[bytes]:
    async for chunk in request.stream():
        yield chunk


async def accept_upload(
    ws: WorkspaceContext,
    request: Request,
    *,
    filename: str,
    kind: str,
    title: str | None,
    collection_id: ObjectId | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    s = get_settings()
    if not ws.can_write:
        raise ValidationFailed("You have read-only access to this workspace.", status_code=403, code="forbidden")
    await ratelimit.hit("upload", str(ws.user_id), limit=s.rate_limit_upload_per_minute, window_seconds=60)
    name = safe_display_name(filename)
    ext = extension_of(name)
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValidationFailed(
            "This file type isn't supported. Upload PDF, TXT, Markdown, JSON, DOCX, HTML or CSV.", code="unsupported_format"
        )
    if kind not in ("book", "document", "auto"):
        raise ValidationFailed("kind must be book, document or auto.")
    cl = request.headers.get("content-length")
    if not cl or not cl.isdigit():
        raise AppError("Uploads must include a Content-Length.", code="length_required", status_code=411)
    declared = int(cl)
    if declared == 0:
        raise ValidationFailed("The file is empty.", code="empty_file")

    r: Resolved = await resolve(ws.account_id)
    max_file = min(r.entitlements.max_file_size_bytes, s.upload_hard_max_bytes)
    if declared > max_file:
        raise FileTooLarge(
            f"This file is {format_bytes(declared)}. Your {r.plan_name} plan allows files up to {format_bytes(max_file)}.",
            details={"size_bytes": declared, "max_file_size_bytes": max_file, "upgrade_path": "/pricing"},
        )

    idem = idempotency_key or f"auto:{ObjectId()}"
    upload_res = await reserve_metric(
        r, "uploads", 1, idempotency_key=f"upload:{ws.account_id}:{idem}", ttl_seconds=3600, meta={"filename": name}
    )
    if upload_res.replayed:
        existing = await get_db().files.find_one({"upload_reservation_id": upload_res.id})
        if existing:
            return {"file": existing, "job_id": existing.get("ingest_job_id"), "replayed": True}
        raise Conflict("This upload is already in progress.", code="upload_in_progress")
    try:
        storage_res = await reserve_metric(
            r, "storage_bytes", declared, idempotency_key=f"storage:{upload_res.id}", ttl_seconds=3600, meta={"filename": name}
        )
    except Exception:
        await usage.release(upload_res.id, reason="storage_quota")
        raise

    db = get_db()
    now = utcnow()
    file_id = ObjectId()
    key = new_storage_key(ws.workspace_id)
    storage = await active_storage()
    file_doc: dict[str, Any] = {
        "_id": file_id,
        "owner_id": ws.account_id,
        "uploaded_by": ws.user_id,
        "workspace_id": ws.workspace_id,
        "original_filename": name,
        "display_title": (title or name.rsplit(".", 1)[0])[:200],
        "title_source": "user" if title else "filename",
        "extension": ext,
        "kind": kind if kind != "auto" else None,
        "requested_kind": kind,
        "collection_ids": [collection_id] if collection_id else [],
        "storage_key": key,
        "storage_backend": storage.name,
        "storage_status": "uploading",
        "storage_committed": False,
        "declared_size_bytes": declared,
        "original_size_bytes": 0,
        "stored_size_bytes": None,
        "processing_status": "uploading",
        "upload_reservation_id": upload_res.id,
        "storage_reservation_id": storage_res.id,
        "upload_period_key": r.period.key,
        "license_note": None,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "quota_released_at": None,
    }
    await db.files.insert_one(file_doc)

    async def reject(code: str, message: str, status: int = 422) -> None:
        await storage.delete(key)
        await db.files.update_one(
            {"_id": file_id},
            {"$set": {"storage_status": "rejected", "processing_status": "rejected", "failure_reason": code, "updated_at": utcnow()}},
        )
        await usage.release(storage_res.id, reason=code)
        await usage.release(upload_res.id, reason=code)
        raise ValidationFailed(message, code=code, status_code=status)

    try:
        stored = await storage.put_stream(key, _stream(request), max_bytes=min(declared, max_file), content_type="application/octet-stream")
    except UploadTooLarge:
        await reject("file_too_large", "The upload exceeded its declared size or your plan's file-size limit.", 413)
        raise  # unreachable
    except Exception as exc:
        log.warning("upload_stream_failed", extra={"error": type(exc).__name__})
        await db.files.update_one(
            {"_id": file_id},
            {
                "$set": {
                    "storage_status": "failed",
                    "processing_status": "failed",
                    "failure_reason": "stream_failed",
                    "updated_at": utcnow(),
                }
            },
        )
        await storage.delete(key)
        await usage.release(storage_res.id, reason="stream_failed")
        await usage.release(upload_res.id, reason="stream_failed")
        raise AppError("The upload was interrupted. Please try again.", code="upload_interrupted", status_code=400, retryable=True) from exc

    if stored.size_bytes == 0:
        await reject("empty_file", "The file is empty.")
    try:
        detected = detect_from_head(name, stored.head)
        if detected.file_type == "docx":
            validate_docx_bytes(await storage.read_bytes(key, max_bytes=max_file))
    except DetectionError as exc:
        await reject(exc.code, exc.message)
        raise  # unreachable
    dup = await db.files.find_one(
        {"workspace_id": ws.workspace_id, "checksum_sha256": stored.sha256, "storage_status": {"$in": ["stored"]}, "_id": {"$ne": file_id}},
        projection={"_id": 1, "display_title": 1},
    )
    if dup:
        await reject("duplicate_file", f"This exact file is already in your library ({dup.get('display_title')}).", 409)

    # Commit storage first (ledger), then mark the file stored/committed.
    await usage.commit(storage_res.id, stored.size_bytes, meta={"file_id": str(file_id)})
    await db.files.update_one(
        {"_id": file_id},
        {
            "$set": {
                "storage_status": "stored",
                "storage_committed": True,
                "original_size_bytes": stored.size_bytes,
                "checksum_sha256": stored.sha256,
                "file_type": detected.file_type,
                "mime_type": detected.mime_type,
                "processing_status": "queued",
                "updated_at": utcnow(),
            }
        },
    )
    await usage.commit(upload_res.id, 1, meta={"file_id": str(file_id)})
    priority = 10 if r.has_feature("priority_processing") else 0
    job_id = await queue.enqueue(
        "ingest.file",
        {"file_id": str(file_id)},
        account_id=ws.account_id,
        workspace_id=ws.workspace_id,
        priority=priority,
        dedupe_key=f"ingest:{file_id}",
        timeout_seconds=3600,
    )
    await db.files.update_one({"_id": file_id}, {"$set": {"ingest_job_id": job_id}})
    await audit.record(
        "file.uploaded",
        actor_id=ws.user_id,
        target_type="file",
        target_id=file_id,
        details={"bytes": stored.size_bytes, "type": detected.file_type},
    )
    return {"file": await db.files.find_one({"_id": file_id}), "job_id": job_id, "replayed": False}
