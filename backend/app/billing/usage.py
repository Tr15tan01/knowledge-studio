"""Concurrency-safe usage reservations and settlement.

Design (works with or without MongoDB multi-document transactions):

* ``usage_events`` is the append-only ledger and the source of truth for consumed usage.
* ``usage_reservations`` records in-flight holds. Each has a unique idempotency key.
* ``usage_counters`` is a per-(account, metric, period) cache that makes the quota check a
  single atomic conditional update::

      {$expr: used + reserved + amount <= limit}  ->  $inc reserved

  so concurrent requests can never jointly exceed a limit.

State machine for a reservation::

    applying -> pending -> committed
                        -> released
             -> denied

Every transition is a guarded ``find_one_and_update`` on the expected previous state, so
retries and duplicate deliveries are idempotent and can never double-charge or
double-release. ``reconcile_counter`` rebuilds a counter from the ledger and pending
reservations if a process crashed between steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.billing.periods import UsagePeriod
from app.core.errors import Conflict, QuotaExceeded
from app.core.timeutil import ensure_utc, utcnow
from app.database.client import get_db

log = logging.getLogger("app.billing.usage")

METRIC_LABELS = {
    "uploads": "uploads",
    "storage_bytes": "storage",
    "questions": "AI questions",
    "research_reports": "research reports",
    "concurrent_research": "concurrent research jobs",
    "concurrent_jobs": "concurrent processing jobs",
    "compute_daily": "daily AI compute budget",
    "compute_monthly": "monthly AI compute budget",
    "cost_daily_micro_usd": "daily AI spending limit",
    "cost_monthly_micro_usd": "monthly AI spending limit",
    "platform_cost_micro_usd": "platform AI spending limit",
}

PLATFORM_ACCOUNT = "__platform__"


def counter_id(account_id: Any, metric: str, period_key: str) -> str:
    return f"{account_id}|{metric}|{period_key}"


@dataclass
class Reservation:
    id: ObjectId
    account_id: Any
    metric: str
    amount: int
    period_key: str
    idempotency_key: str
    state: str
    replayed: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


async def _ensure_counter(cid: str, account_id: Any, metric: str, period: UsagePeriod) -> None:
    await get_db().usage_counters.update_one(
        {"_id": cid},
        {
            "$setOnInsert": {
                "account_id": account_id,
                "metric": metric,
                "period_key": period.key,
                "period_start": period.start,
                "period_end": period.end,
                "used": 0,
                "reserved": 0,
                "version": 0,
                "created_at": utcnow(),
            }
        },
        upsert=True,
    )


async def reserve(
    *,
    account_id: Any,
    metric: str,
    amount: int,
    limit: int | None,
    period: UsagePeriod,
    idempotency_key: str,
    ttl: timedelta = timedelta(minutes=30),
    meta: dict[str, Any] | None = None,
    reset_at: datetime | None = None,
) -> Reservation:
    """Atomically reserve ``amount`` against ``limit``. ``limit=None`` means unmetered (tracked only)."""
    if amount < 0:
        raise ValueError("amount must be >= 0")
    db = get_db()
    cid = counter_id(account_id, metric, period.key)
    now = utcnow()
    doc = {
        "account_id": account_id,
        "metric": metric,
        "amount": int(amount),
        "period_key": period.key,
        "counter_id": cid,
        "state": "applying",
        "idempotency_key": idempotency_key,
        "meta": meta or {},
        "created_at": now,
        "updated_at": now,
        "expires_at": now + ttl,
    }
    try:
        res = await db.usage_reservations.insert_one(doc)
        rid = res.inserted_id
    except DuplicateKeyError:
        existing = await db.usage_reservations.find_one({"idempotency_key": idempotency_key})
        if existing is None:  # pragma: no cover - race with deletion
            raise Conflict("Duplicate request; please retry.") from None
        if existing["state"] == "denied":
            raise _quota_error(metric, limit, await _current(cid), reset_at) from None
        return Reservation(
            existing["_id"],
            account_id,
            metric,
            existing["amount"],
            existing["period_key"],
            idempotency_key,
            existing["state"],
            replayed=True,
            meta=existing.get("meta", {}),
        )

    await _ensure_counter(cid, account_id, metric, period)
    flt: dict[str, Any] = {"_id": cid}
    if limit is not None:
        flt["$expr"] = {"$lte": [{"$add": ["$used", "$reserved", int(amount)]}, int(limit)]}
    updated = await db.usage_counters.find_one_and_update(
        flt, {"$inc": {"reserved": int(amount), "version": 1}}, return_document=ReturnDocument.AFTER
    )
    if updated is None:
        await db.usage_reservations.update_one({"_id": rid, "state": "applying"}, {"$set": {"state": "denied", "updated_at": utcnow()}})
        await _record_denial(account_id, metric, amount, limit, period.key)
        raise _quota_error(metric, limit, await _current(cid), reset_at)
    await db.usage_reservations.update_one({"_id": rid, "state": "applying"}, {"$set": {"state": "pending", "updated_at": utcnow()}})
    return Reservation(rid, account_id, metric, int(amount), period.key, idempotency_key, "pending", meta=meta or {})


async def commit(reservation_id: ObjectId, actual_amount: int | None = None, *, meta: dict[str, Any] | None = None) -> bool:
    """Convert a pending hold into consumed usage. Returns False if already settled."""
    db = get_db()
    now = utcnow()
    res = await db.usage_reservations.find_one_and_update(
        {"_id": reservation_id, "state": "pending"},
        {"$set": {"state": "committed", "committed_amount": actual_amount, "updated_at": now, "settled_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if res is None:
        return False
    amount = int(res["amount"] if actual_amount is None else actual_amount)
    try:
        await db.usage_events.insert_one(
            {
                "account_id": res["account_id"],
                "metric": res["metric"],
                "amount": amount,
                "period_key": res["period_key"],
                "reservation_id": res["_id"],
                "idempotency_key": f"res:{res['_id']}",
                "meta": {**res.get("meta", {}), **(meta or {})},
                "occurred_at": now,
            }
        )
    except DuplicateKeyError:  # pragma: no cover - only on crash-retry
        pass
    await db.usage_counters.update_one(
        {"_id": res["counter_id"]}, {"$inc": {"used": amount, "reserved": -int(res["amount"]), "version": 1}}
    )
    return True


async def release(reservation_id: ObjectId, *, reason: str = "released") -> bool:
    """Return a pending hold. Idempotent: returns False when already settled."""
    db = get_db()
    res = await db.usage_reservations.find_one_and_update(
        {"_id": reservation_id, "state": "pending"},
        {"$set": {"state": "released", "release_reason": reason, "updated_at": utcnow(), "settled_at": utcnow()}},
    )
    if res is None:
        return False
    await db.usage_counters.update_one({"_id": res["counter_id"]}, {"$inc": {"reserved": -int(res["amount"]), "version": 1}})
    return True


async def record_usage(
    *, account_id: Any, metric: str, amount: int, period: UsagePeriod, idempotency_key: str, meta: dict[str, Any] | None = None
) -> bool:
    """Record usage that was not reserved in advance (e.g. storage release, admin corrections)."""
    db = get_db()
    cid = counter_id(account_id, metric, period.key)
    try:
        await db.usage_events.insert_one(
            {
                "account_id": account_id,
                "metric": metric,
                "amount": int(amount),
                "period_key": period.key,
                "idempotency_key": idempotency_key,
                "meta": meta or {},
                "occurred_at": utcnow(),
            }
        )
    except DuplicateKeyError:
        return False
    await _ensure_counter(cid, account_id, metric, period)
    await db.usage_counters.update_one({"_id": cid}, {"$inc": {"used": int(amount), "version": 1}})
    return True


async def _current(cid: str) -> dict[str, Any]:
    return await get_db().usage_counters.find_one({"_id": cid}) or {"used": 0, "reserved": 0}


async def usage_snapshot(account_id: Any, metric: str, period: UsagePeriod) -> dict[str, int]:
    c = await _current(counter_id(account_id, metric, period.key))
    return {"used": int(c.get("used", 0)), "reserved": int(c.get("reserved", 0))}


async def _record_denial(account_id: Any, metric: str, amount: int, limit: int | None, period_key: str) -> None:
    await get_db().usage_events.insert_one(
        {
            "account_id": account_id,
            "metric": "quota_denial",
            "amount": 0,
            "period_key": period_key,
            "meta": {"metric": metric, "requested": amount, "limit": limit},
            "occurred_at": utcnow(),
        }
    )


def _quota_error(metric: str, limit: int | None, counter: dict[str, Any], reset_at: datetime | None) -> QuotaExceeded:
    label = METRIC_LABELS.get(metric, metric)
    used = int(counter.get("used", 0)) + int(counter.get("reserved", 0))
    if metric == "storage_bytes":
        msg = "Not enough storage remaining for this file. Delete files or upgrade your plan."
    elif metric.startswith("concurrent"):
        msg = f"You've reached the limit of {limit} {label}. Wait for a running job to finish or cancel one."
    elif metric.startswith("platform"):
        msg = "AI processing is temporarily unavailable because the platform spending limit was reached. Please try again later."
    elif metric.startswith(("compute", "cost")):
        msg = f"You've reached your {label}. It resets automatically; upgrading raises the limit."
    else:
        msg = f"You've used all {limit} {label} for this period."
    return QuotaExceeded(
        msg,
        details={
            "metric": metric,
            "limit": limit,
            "used": used,
            "reset_at": reset_at.isoformat() if reset_at else None,
            "upgrade_path": "/pricing",
        },
    )


# --- Reconciliation ---------------------------------------------------------------------


async def expire_stale_reservations(*, grace: timedelta = timedelta(seconds=60)) -> int:
    """Release pending holds whose TTL elapsed and clean up crashed 'applying' rows."""
    db = get_db()
    now = utcnow()
    n = 0
    async for r in db.usage_reservations.find({"state": "pending", "expires_at": {"$lt": now}}).limit(5000):
        if await release(r["_id"], reason="expired"):
            n += 1
    touched: set[str] = set()
    async for r in db.usage_reservations.find({"state": "applying", "created_at": {"$lt": now - grace}}).limit(5000):
        res = await db.usage_reservations.update_one(
            {"_id": r["_id"], "state": "applying"}, {"$set": {"state": "released", "release_reason": "abandoned", "updated_at": now}}
        )
        if res.modified_count:
            touched.add(r["counter_id"])
            n += 1
    for cid in touched:  # we don't know whether the counter increment happened: rebuild
        await reconcile_counter(cid)
    return n


async def repair_committed_events(cid: str) -> int:
    """Insert ledger events for committed reservations that are missing one (crash between steps)."""
    db = get_db()
    repaired = 0
    async for r in db.usage_reservations.find({"counter_id": cid, "state": "committed"}):
        amt = r.get("committed_amount")
        try:
            await db.usage_events.insert_one(
                {
                    "account_id": r["account_id"],
                    "metric": r["metric"],
                    "amount": int(r["amount"] if amt is None else amt),
                    "period_key": r["period_key"],
                    "reservation_id": r["_id"],
                    "idempotency_key": f"res:{r['_id']}",
                    "meta": {**r.get("meta", {}), "repaired": True},
                    "occurred_at": r.get("settled_at") or utcnow(),
                }
            )
            repaired += 1
        except DuplicateKeyError:
            pass
    return repaired


async def reconcile_counter(cid: str, *, attempts: int = 5) -> dict[str, Any] | None:
    """Rebuild a counter from the ledger and pending reservations (optimistic concurrency)."""
    db = get_db()
    for _ in range(attempts):
        counter = await db.usage_counters.find_one({"_id": cid})
        if counter is None:
            return None
        await repair_committed_events(cid)
        used = 0
        async for row in await db.usage_events.aggregate(
            [
                {"$match": {"account_id": counter["account_id"], "metric": counter["metric"], "period_key": counter["period_key"]}},
                {"$group": {"_id": None, "s": {"$sum": "$amount"}}},
            ]
        ):
            used = int(row["s"])
        reserved = 0
        async for row in await db.usage_reservations.aggregate(
            [{"$match": {"counter_id": cid, "state": "pending"}}, {"$group": {"_id": None, "s": {"$sum": "$amount"}}}]
        ):
            reserved = int(row["s"])
        if counter.get("used") == used and counter.get("reserved") == reserved:
            return counter
        res = await db.usage_counters.update_one(
            {"_id": cid, "version": counter["version"]},
            {"$set": {"used": used, "reserved": reserved, "reconciled_at": utcnow()}, "$inc": {"version": 1}},
        )
        if res.modified_count:
            log.warning(
                "usage_counter_repaired",
                extra={
                    "counter": cid,
                    "old_used": counter.get("used"),
                    "used": used,
                    "old_reserved": counter.get("reserved"),
                    "reserved": reserved,
                },
            )
            await db.reconciliation_reports.insert_one(
                {
                    "kind": "usage_counter",
                    "counter_id": cid,
                    "before": {"used": counter.get("used"), "reserved": counter.get("reserved")},
                    "after": {"used": used, "reserved": reserved},
                    "created_at": utcnow(),
                }
            )
            return await db.usage_counters.find_one({"_id": cid})
    log.error("usage_counter_reconcile_contended", extra={"counter": cid})
    return None


def ttl_for(seconds: int) -> timedelta:
    return timedelta(seconds=max(60, seconds))


def is_expired(r: dict[str, Any]) -> bool:
    exp = ensure_utc(r.get("expires_at"))
    return bool(exp and exp < utcnow())
