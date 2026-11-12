from __future__ import annotations

import asyncio
from datetime import timedelta

from app.core.timeutil import utcnow
from app.database.client import mongo
from app.jobs import queue
from app.jobs.runtime import JobContext, PermanentJobError, RetryableJobError, handler, run_one

CALLS: dict[str, int] = {}


@handler("test.ok")
async def _ok(ctx: JobContext):
    CALLS["ok"] = CALLS.get("ok", 0) + 1
    await ctx.emit("phase", {"phase": "work"}, phase="work")
    return {"done": True}


@handler("test.flaky")
async def _flaky(ctx: JobContext):
    CALLS["flaky"] = CALLS.get("flaky", 0) + 1
    raise RetryableJobError("provider_timeout", "try later")


@handler("test.broken")
async def _broken(ctx: JobContext):
    raise PermanentJobError("bad_input", "no")


@handler("test.slow")
async def _slow(ctx: JobContext):
    for _ in range(50):
        await ctx.checkpoint()
        await asyncio.sleep(0.01)
    return {}


async def test_atomic_claim_single_winner():
    jid = await queue.enqueue("test.ok", {})
    claims = await asyncio.gather(*(queue.claim(f"w{i}", ["test.ok"]) for i in range(20)))
    winners = [c for c in claims if c]
    assert len(winners) == 1 and winners[0]["_id"] == jid


async def test_dedupe_key_idempotent_enqueue():
    a = await queue.enqueue("test.ok", {}, dedupe_key="same")
    b = await queue.enqueue("test.ok", {}, dedupe_key="same")
    assert a == b and await mongo.db.jobs.count_documents({}) == 1


async def test_complete_and_events():
    jid = await queue.enqueue("test.ok", {})
    job = await queue.claim("w", ["test.ok"])
    assert await run_one(job, "w") == "completed"
    j = await mongo.db.jobs.find_one({"_id": jid})
    assert j["status"] == "completed" and j["result"] == {"done": True}
    events = await queue.events_since(jid, 0)
    assert [e["seq"] for e in events] == [1] and events[0]["data"]["phase"] == "work"


async def test_retry_with_backoff_then_failed():
    jid = await queue.enqueue("test.flaky", {}, max_attempts=2)
    job = await queue.claim("w", ["test.flaky"])
    assert await run_one(job, "w") == "retrying"
    j = await mongo.db.jobs.find_one({"_id": jid})
    assert j["status"] == "retrying" and j["run_after"] >= utcnow() - timedelta(seconds=1)
    await mongo.db.jobs.update_one({"_id": jid}, {"$set": {"run_after": utcnow()}})
    job = await queue.claim("w", ["test.flaky"])
    assert await run_one(job, "w") == "failed"
    j = await mongo.db.jobs.find_one({"_id": jid})
    assert j["status"] == "failed" and j["error"]["code"] == "provider_timeout" and len(j["error_history"]) == 2


async def test_permanent_failure_not_retried():
    jid = await queue.enqueue("test.broken", {})
    job = await queue.claim("w", ["test.broken"])
    assert await run_one(job, "w") == "failed"
    assert (await mongo.db.jobs.find_one({"_id": jid}))["attempts"] == 1


async def test_abandoned_job_recovered_after_lease_expiry():
    jid = await queue.enqueue("test.ok", {})
    job = await queue.claim("crashed-worker", ["test.ok"])
    assert job
    # Worker dies: lease expires without heartbeat.
    await mongo.db.jobs.update_one({"_id": jid}, {"$set": {"lease_expires_at": utcnow() - timedelta(seconds=1)}})
    rec = await queue.claim("healthy-worker", ["test.ok"])
    assert rec and rec["_id"] == jid and rec["attempts"] == 2 and rec.get("recovered_at")
    # The crashed worker can no longer complete it.
    assert not await queue.complete(jid, "crashed-worker", {})
    assert await run_one(rec, "healthy-worker") == "completed"


async def test_exhausted_abandoned_job_fails_permanently():
    jid = await queue.enqueue("test.ok", {}, max_attempts=1)
    await queue.claim("w", ["test.ok"])
    await mongo.db.jobs.update_one({"_id": jid}, {"$set": {"lease_expires_at": utcnow() - timedelta(seconds=1)}})
    assert await queue.claim("w2", ["test.ok"]) is None
    assert await queue.fail_exhausted_abandoned() == 1
    assert (await mongo.db.jobs.find_one({"_id": jid}))["status"] == "failed"


async def test_cancel_queued_and_running():
    q = await queue.enqueue("test.slow", {})
    assert await queue.request_cancel(q) == "cancelled"
    assert await queue.claim("w", ["test.slow"]) is None
    r = await queue.enqueue("test.slow", {})
    job = await queue.claim("w", ["test.slow"])
    assert await queue.request_cancel(r) == "cancel_requested"
    assert await run_one(job, "w") == "cancelled"
    assert (await mongo.db.jobs.find_one({"_id": r}))["status"] == "cancelled"


async def test_admin_retry_failed_is_same_job():
    jid = await queue.enqueue("test.broken", {})
    await run_one(await queue.claim("w", ["test.broken"]), "w")
    assert await queue.retry_failed(jid)
    assert not await queue.retry_failed(jid)  # only from failed state: no duplicates
    assert await mongo.db.jobs.count_documents({}) == 1
