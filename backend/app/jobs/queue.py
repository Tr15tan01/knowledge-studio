"""Durable MongoDB-backed job queue.

Semantics:
* Atomic claiming with ``find_one_and_update`` (one worker wins).
* Leases: a claimed job carries ``lease_expires_at``; workers heartbeat to extend it.
  A job whose lease expired (worker crash) becomes claimable again.
* Bounded retries with exponential backoff and full jitter; permanent failures stop.
* ``dedupe_key`` (unique partial index) makes enqueueing idempotent.
* Cancellation: queued jobs cancel immediately; running jobs see ``cancel_requested`` on
  their next heartbeat/checkpoint and stop at a safe point.
* Progress events are appended to ``job_events`` with a per-job sequence number so clients
  can resume streams after reconnecting.
"""

from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.database.client import get_db

log = logging.getLogger("app.jobs")

TERMINAL = ("completed", "failed", "cancelled")
CLAIMABLE = ("queued", "retrying")


async def enqueue(
    job_type: str,
    payload: dict[str, Any],
    *,
    account_id: Any = None,
    workspace_id: ObjectId | None = None,
    priority: int = 0,
    dedupe_key: str | None = None,
    delay_seconds: float = 0,
    max_attempts: int | None = None,
    timeout_seconds: int = 1800,
) -> ObjectId:
    now = utcnow()
    doc = {
        "type": job_type,
        "payload": payload,
        "account_id": account_id,
        "workspace_id": workspace_id,
        "status": "queued",
        "priority": priority,
        "attempts": 0,
        "max_attempts": max_attempts or get_settings().job_max_attempts,
        "run_after": now + timedelta(seconds=delay_seconds),
        "lease_expires_at": None,
        "worker_id": None,
        "cancel_requested": False,
        "progress": {"phase": "queued", "completed_steps": 0, "total_steps": None},
        "event_seq": 0,
        "error": None,
        "result": None,
        "timeout_seconds": timeout_seconds,
        "created_at": now,
        "updated_at": now,
    }
    if dedupe_key:
        doc["dedupe_key"] = dedupe_key
    try:
        res = await get_db().jobs.insert_one(doc)
        return res.inserted_id
    except DuplicateKeyError:
        existing = await get_db().jobs.find_one({"dedupe_key": dedupe_key})
        assert existing is not None
        return existing["_id"]


async def claim(worker_id: str, types: list[str] | None = None) -> dict[str, Any] | None:
    s = get_settings()
    now = utcnow()
    type_filter: dict[str, Any] = {"type": {"$in": types}} if types else {}
    lease = now + timedelta(seconds=s.job_lease_seconds)
    common_set = {"status": "running", "worker_id": worker_id, "lease_expires_at": lease, "heartbeat_at": now, "updated_at": now}
    # 1) Fresh or retrying jobs.
    job = await get_db().jobs.find_one_and_update(
        {"status": {"$in": list(CLAIMABLE)}, "run_after": {"$lte": now}, "cancel_requested": False, **type_filter},
        {"$set": {**common_set, "started_at": now}, "$inc": {"attempts": 1}},
        sort=[("priority", -1), ("run_after", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if job:
        return job
    # 2) Abandoned jobs whose lease expired (worker crash / restart).
    job = await get_db().jobs.find_one_and_update(
        {"status": "running", "lease_expires_at": {"$lt": now}, "$expr": {"$lt": ["$attempts", "$max_attempts"]}, **type_filter},
        {"$set": {**common_set, "recovered_at": now}, "$inc": {"attempts": 1}},
        sort=[("priority", -1), ("lease_expires_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if job:
        log.warning("job_recovered_after_lease_expiry", extra={"job_type": job["type"], "attempt": job["attempts"], "job": str(job["_id"])})
    return job


async def requeue_without_attempt(job_id: ObjectId, worker_id: str, delay_seconds: float) -> None:
    """Give a claimed job back (e.g. per-account concurrency limit) without consuming an attempt."""
    await get_db().jobs.update_one(
        {"_id": job_id, "worker_id": worker_id, "status": "running"},
        {
            "$set": {
                "status": "queued",
                "worker_id": None,
                "lease_expires_at": None,
                "run_after": utcnow() + timedelta(seconds=delay_seconds),
                "updated_at": utcnow(),
            },
            "$inc": {"attempts": -1},
        },
    )


async def heartbeat(job_id: ObjectId, worker_id: str) -> tuple[bool, bool]:
    """Extend the lease. Returns (still_owner, cancel_requested)."""
    now = utcnow()
    job = await get_db().jobs.find_one_and_update(
        {"_id": job_id, "worker_id": worker_id, "status": "running"},
        {"$set": {"lease_expires_at": now + timedelta(seconds=get_settings().job_lease_seconds), "heartbeat_at": now}},
        projection={"cancel_requested": 1},
        return_document=ReturnDocument.AFTER,
    )
    if not job:
        return False, True
    return True, bool(job.get("cancel_requested"))


async def complete(job_id: ObjectId, worker_id: str, result: dict[str, Any] | None = None) -> bool:
    res = await get_db().jobs.update_one(
        {"_id": job_id, "worker_id": worker_id, "status": "running"},
        {
            "$set": {
                "status": "completed",
                "result": result or {},
                "finished_at": utcnow(),
                "updated_at": utcnow(),
                "lease_expires_at": None,
                "progress.phase": "completed",
            }
        },
    )
    return res.modified_count == 1


def backoff_seconds(attempt: int, base: float = 5.0, cap: float = 600.0) -> float:
    """Exponential backoff with full jitter."""
    return random.uniform(0, min(cap, base * (2 ** max(0, attempt - 1))))


async def fail(job_id: ObjectId, worker_id: str, *, code: str, message: str, retryable: bool, details: dict[str, Any] | None = None) -> str:
    db = get_db()
    job = await db.jobs.find_one({"_id": job_id, "worker_id": worker_id, "status": "running"})
    if not job:
        return "not_owner"
    error = {"code": code, "message": message[:500], "retryable": retryable, "details": details or {}, "at": utcnow()}
    if retryable and job["attempts"] < job["max_attempts"]:
        delay = backoff_seconds(job["attempts"])
        await db.jobs.update_one(
            {"_id": job_id, "worker_id": worker_id, "status": "running"},
            {
                "$set": {
                    "status": "retrying",
                    "error": error,
                    "run_after": utcnow() + timedelta(seconds=delay),
                    "worker_id": None,
                    "lease_expires_at": None,
                    "updated_at": utcnow(),
                },
                "$push": {"error_history": {"$each": [error], "$slice": -10}},
            },
        )
        return "retrying"
    await db.jobs.update_one(
        {"_id": job_id, "worker_id": worker_id, "status": "running"},
        {
            "$set": {
                "status": "failed",
                "error": error,
                "finished_at": utcnow(),
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": utcnow(),
                "progress.phase": "failed",
            },
            "$push": {"error_history": {"$each": [error], "$slice": -10}},
        },
    )
    return "failed"


async def mark_cancelled(job_id: ObjectId, worker_id: str | None = None) -> bool:
    q: dict[str, Any] = {"_id": job_id, "status": {"$nin": list(TERMINAL)}}
    if worker_id:
        q["worker_id"] = worker_id
    res = await get_db().jobs.update_one(
        q,
        {
            "$set": {
                "status": "cancelled",
                "finished_at": utcnow(),
                "updated_at": utcnow(),
                "lease_expires_at": None,
                "progress.phase": "cancelled",
            }
        },
    )
    return res.modified_count == 1


async def request_cancel(job_id: ObjectId) -> str:
    """Cancel queued jobs immediately; ask running jobs to stop at their next checkpoint."""
    db = get_db()
    res = await db.jobs.find_one_and_update(
        {"_id": job_id, "status": {"$in": list(CLAIMABLE)}},
        {
            "$set": {
                "status": "cancelled",
                "cancel_requested": True,
                "finished_at": utcnow(),
                "updated_at": utcnow(),
                "progress.phase": "cancelled",
            }
        },
    )
    if res:
        return "cancelled"
    res = await db.jobs.find_one_and_update(
        {"_id": job_id, "status": "running"}, {"$set": {"cancel_requested": True, "updated_at": utcnow()}}
    )
    return "cancel_requested" if res else "not_cancellable"


async def retry_failed(job_id: ObjectId) -> bool:
    """Admin/user retry of a permanently failed job. Same job id → idempotent handlers."""
    res = await get_db().jobs.update_one(
        {"_id": job_id, "status": "failed"},
        {
            "$set": {
                "status": "queued",
                "run_after": utcnow(),
                "cancel_requested": False,
                "updated_at": utcnow(),
                "progress.phase": "queued",
            },
            "$inc": {"max_attempts": 1, "manual_retries": 1},
        },
    )
    return res.modified_count == 1


async def fail_exhausted_abandoned() -> int:
    """Running jobs whose lease expired after the final attempt are failed permanently."""
    now = utcnow()
    res = await get_db().jobs.update_many(
        {"status": "running", "lease_expires_at": {"$lt": now}, "$expr": {"$gte": ["$attempts", "$max_attempts"]}},
        {
            "$set": {
                "status": "failed",
                "finished_at": now,
                "updated_at": now,
                "lease_expires_at": None,
                "error": {
                    "code": "abandoned",
                    "message": "The worker stopped responding and retries were exhausted.",
                    "retryable": False,
                    "at": now,
                },
            }
        },
    )
    return res.modified_count


async def emit(job_id: ObjectId, event_type: str, data: dict[str, Any] | None = None, *, progress: dict[str, Any] | None = None) -> int:
    db = get_db()
    upd: dict[str, Any] = {"$inc": {"event_seq": 1}, "$set": {"updated_at": utcnow()}}
    if progress:
        for k, v in progress.items():
            upd["$set"][f"progress.{k}"] = v
    job = await db.jobs.find_one_and_update({"_id": job_id}, upd, projection={"event_seq": 1}, return_document=ReturnDocument.AFTER)
    if not job:
        return 0
    seq = int(job["event_seq"])
    await db.job_events.insert_one({"job_id": job_id, "seq": seq, "type": event_type, "data": data or {}, "created_at": utcnow()})
    return seq


async def events_since(job_id: ObjectId, after_seq: int, limit: int = 200) -> list[dict[str, Any]]:
    return [e async for e in get_db().job_events.find({"job_id": job_id, "seq": {"$gt": after_seq}}).sort("seq", 1).limit(limit)]


async def queue_stats() -> dict[str, Any]:
    db = get_db()
    out: dict[str, Any] = {"by_status": {}, "by_type": {}}
    async for row in await db.jobs.aggregate([{"$group": {"_id": {"s": "$status", "t": "$type"}, "n": {"$sum": 1}}}]):
        s, t = row["_id"]["s"], row["_id"]["t"]
        out["by_status"][s] = out["by_status"].get(s, 0) + row["n"]
        out["by_type"].setdefault(t, {})[s] = row["n"]
    now = utcnow()
    out["stuck"] = await db.jobs.count_documents({"status": "running", "lease_expires_at": {"$lt": now}})
    oldest = await db.jobs.find_one({"status": {"$in": list(CLAIMABLE)}}, sort=[("run_after", 1)], projection={"run_after": 1})
    out["oldest_queued_seconds"] = (now - oldest["run_after"]).total_seconds() if oldest else 0
    return out
