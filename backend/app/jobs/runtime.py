"""Worker runtime: claims jobs, heartbeats leases, enforces timeouts, routes to handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from bson import ObjectId

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.jobs import queue
from app.observability.logging import job_id_var

log = logging.getLogger("app.worker")


class JobCancelled(Exception):
    pass


class PermanentJobError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class RetryableJobError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class JobContext:
    job: dict[str, Any]
    worker_id: str
    _cancel: bool = False
    _lost: bool = False
    started: float = field(default_factory=time.monotonic)

    @property
    def id(self) -> ObjectId:
        return self.job["_id"]

    @property
    def payload(self) -> dict[str, Any]:
        return self.job["payload"]

    @property
    def attempt(self) -> int:
        return int(self.job.get("attempts", 1))

    async def checkpoint(self) -> None:
        """Call between steps: raises JobCancelled if cancellation was requested or the lease was lost."""
        owner, cancel = await queue.heartbeat(self.id, self.worker_id)
        if not owner:
            self._lost = True
            raise JobCancelled("lease lost")
        if cancel:
            self._cancel = True
            raise JobCancelled("cancel requested")
        if time.monotonic() - self.started > int(self.job.get("timeout_seconds") or 1800):
            raise PermanentJobError("job_timeout", "The job exceeded its maximum execution time.")

    async def emit(self, event_type: str, data: dict[str, Any] | None = None, **progress: Any) -> None:
        await queue.emit(self.id, event_type, data, progress=progress or None)

    @property
    def cancelled(self) -> bool:
        return self._cancel


Handler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]
_HANDLERS: dict[str, Handler] = {}
_ON_FAIL: dict[str, Callable[[dict[str, Any], str, str], Awaitable[None]]] = {}
_ON_CANCEL: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[job_type] = fn
        return fn

    return deco


def on_permanent_failure(job_type: str):  # type: ignore[no-untyped-def]
    def deco(fn):  # type: ignore[no-untyped-def]
        _ON_FAIL[job_type] = fn
        return fn

    return deco


def on_cancel(job_type: str):  # type: ignore[no-untyped-def]
    def deco(fn):  # type: ignore[no-untyped-def]
        _ON_CANCEL[job_type] = fn
        return fn

    return deco


_LOADED = False


def load_handlers() -> dict[str, Handler]:
    global _LOADED
    if _LOADED:
        return _HANDLERS
    _LOADED = True
    # Import modules that register handlers.
    from app.documents import (
        deletion,  # noqa: F401
        ingest,  # noqa: F401
    )
    from app.graph import extraction  # noqa: F401
    from app.jobs import maintenance  # noqa: F401
    from app.research import agent  # noqa: F401
    from app.retrieval import reindex  # noqa: F401
    from app.services import account_jobs  # noqa: F401

    return _HANDLERS


async def _heartbeat_loop(ctx: JobContext, stop: asyncio.Event) -> None:
    interval = get_settings().job_heartbeat_seconds
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            owner, cancel = await queue.heartbeat(ctx.id, ctx.worker_id)
            if not owner:
                ctx._lost = True
            if cancel:
                ctx._cancel = True


async def _over_account_limit(job: dict[str, Any]) -> bool:
    acct = job.get("account_id")
    if acct is None:
        return False
    from app.billing.entitlements import resolve

    limit = (await resolve(acct)).entitlements.max_concurrent_jobs
    running = await get_db().jobs.count_documents({"account_id": acct, "status": "running", "_id": {"$ne": job["_id"]}})
    return running >= limit


async def run_one(job: dict[str, Any], worker_id: str) -> str:
    handlers = load_handlers()
    fn = handlers.get(job["type"])
    token = job_id_var.set(str(job["_id"]))
    ctx = JobContext(job, worker_id)
    try:
        if fn is None:
            await queue.fail(job["_id"], worker_id, code="unknown_job_type", message=f"No handler for {job['type']}", retryable=False)
            return "failed"
        if job["type"] not in ("maintenance.reconcile",) and await _over_account_limit(job):
            await queue.requeue_without_attempt(job["_id"], worker_id, delay_seconds=5)
            return "deferred"
        stop = asyncio.Event()
        hb = asyncio.create_task(_heartbeat_loop(ctx, stop))
        try:
            result = await fn(ctx)
        finally:
            stop.set()
            await hb
        if ctx._lost:
            log.warning("job_lease_lost_result_discarded")
            return "lost"
        await queue.complete(job["_id"], worker_id, result)
        return "completed"
    except JobCancelled:
        if ctx._lost and not ctx._cancel:
            return "lost"
        if cancel_cb := _ON_CANCEL.get(job["type"]):
            await cancel_cb(job)
        await queue.mark_cancelled(job["_id"], worker_id)
        return "cancelled"
    except PermanentJobError as exc:
        status = await queue.fail(job["_id"], worker_id, code=exc.code, message=exc.message, retryable=False, details=exc.details)
        if status == "failed" and (cb := _ON_FAIL.get(job["type"])):
            await cb(job, exc.code, exc.message)
        return status
    except (RetryableJobError, AppError) as exc:
        retryable = isinstance(exc, RetryableJobError) or bool(getattr(exc, "retryable", False))
        code = exc.code
        msg = exc.message
        status = await queue.fail(job["_id"], worker_id, code=code, message=msg, retryable=retryable)
        if status == "failed" and (cb := _ON_FAIL.get(job["type"])):
            await cb(job, code, msg)
        return status
    except Exception as exc:  # noqa: BLE001
        log.exception("job_crashed", extra={"job_type": job["type"]})
        status = await queue.fail(
            job["_id"], worker_id, code="internal_error", message=f"Unexpected error ({type(exc).__name__}).", retryable=True
        )
        if status == "failed" and (cb := _ON_FAIL.get(job["type"])):
            await cb(job, "internal_error", "Unexpected error.")
        return status
    finally:
        job_id_var.reset(token)


class Worker:
    def __init__(self, *, concurrency: int | None = None, types: list[str] | None = None) -> None:
        s = get_settings()
        self.id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.concurrency = concurrency or s.worker_concurrency
        self.types = types
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def _beat(self) -> None:
        await get_db().worker_heartbeats.update_one(
            {"_id": self.id},
            {"$set": {"seen_at": utcnow(), "expires_at": utcnow() + timedelta(seconds=90), "concurrency": self.concurrency}},
            upsert=True,
        )

    async def _slot(self, n: int) -> None:
        poll = get_settings().job_poll_interval_seconds
        while not self._stop.is_set():
            try:
                job = await queue.claim(self.id, self.types)
            except Exception:  # noqa: BLE001 - database blip: back off and keep the worker alive
                log.exception("claim_failed")
                await asyncio.sleep(5)
                continue
            if not job:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=poll)
                except TimeoutError:
                    pass
                continue
            t0 = time.perf_counter()
            status = await run_one(job, self.id)
            log.info(
                "job_finished",
                extra={
                    "job_type": job["type"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - t0) * 1000),
                    "attempt": job.get("attempts"),
                },
            )

    async def _maintenance(self) -> None:
        while not self._stop.is_set():
            try:
                await self._beat()
                await queue.fail_exhausted_abandoned()
                bucket = utcnow().strftime("%Y%m%d%H") + f"{utcnow().minute // 15:02d}"
                await queue.enqueue("maintenance.reconcile", {}, dedupe_key=f"reconcile:{bucket}", priority=-5)
            except Exception:  # noqa: BLE001
                log.exception("maintenance_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except TimeoutError:
                pass

    async def run(self) -> None:
        load_handlers()
        log.info("worker_started", extra={"worker": self.id, "concurrency": self.concurrency})
        tasks = [asyncio.create_task(self._slot(i)) for i in range(self.concurrency)]
        tasks.append(asyncio.create_task(self._maintenance()))
        await self._stop.wait()
        log.info("worker_stopping_gracefully")
        # Running jobs finish their current step; unfinished ones are recovered via lease expiry.
        done, pending = await asyncio.wait(tasks, timeout=max(5, get_settings().job_lease_seconds // 2))
        for t in pending:
            t.cancel()
        await get_db().worker_heartbeats.delete_one({"_id": self.id})
