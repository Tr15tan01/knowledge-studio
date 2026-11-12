"""Admin metrics derived from real records. Estimates are labelled as estimates."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.timeutil import utcnow
from app.database.client import get_db

DAY_FMT = {"$dateToString": {"format": "%Y-%m-%d", "date": "$occurred_at"}}


async def _count_series(coll: str, match: dict[str, Any], date_field: str, since: datetime) -> dict[str, int]:
    out: dict[str, int] = {}
    async for row in await get_db()[coll].aggregate(
        [
            {"$match": {**match, date_field: {"$gte": since}}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": f"${date_field}"}}, "n": {"$sum": 1}}},
        ]
    ):
        out[row["_id"]] = row["n"]
    return out


async def mrr_estimate() -> dict[str, Any]:
    """Monthly recurring revenue estimate from active paid subscriptions and their verified price snapshots."""
    db = get_db()
    totals: dict[str, float] = {}
    subs = 0
    async for sub in db.subscriptions.find(
        {"source": "paddle", "status": {"$in": ["active", "past_due", "trialing"]}, "plan_id": {"$ne": None}}
    ):
        if sub.get("status") == "trialing":
            continue
        m = await db.price_mappings.find_one({"paddle_price_id": sub.get("paddle_price_id")})
        snap = (m or {}).get("verified_price") or {}
        amount = snap.get("unit_amount_minor")
        if amount is None:
            continue
        cur = snap.get("currency") or sub.get("currency") or "USD"
        monthly = amount / (12 if (sub.get("billing_interval") or (m or {}).get("interval")) == "year" else 1)
        totals[cur] = totals.get(cur, 0) + monthly
        subs += 1
    return {
        "by_currency_minor": {k: round(v) for k, v in totals.items()},
        "subscriptions_counted": subs,
        "is_estimate": True,
        "basis": "Verified Paddle list prices of active subscriptions (annual / 12). Excludes discounts, taxes and refunds; Paddle reports are authoritative.",
    }


async def overview(days: int = 30) -> dict[str, Any]:
    db = get_db()
    now = utcnow()
    since = now - timedelta(days=days)
    total_users = await db.users.count_documents({"status": {"$ne": "deleting"}})
    new_users = await db.users.count_documents({"created_at": {"$gte": since}})
    active_ids = set(await db.sessions.distinct("user_id", {"last_seen_at": {"$gte": since}}))
    plan_dist: dict[str, dict[str, int]] = {}
    plan_names = {p["_id"]: p["slug"] async for p in db.plans.find({}, projection={"slug": 1})}
    async for row in await db.subscriptions.aggregate(
        [{"$group": {"_id": {"plan": "$plan_id", "status": "$status", "source": "$source"}, "n": {"$sum": 1}}}]
    ):
        slug = plan_names.get(row["_id"]["plan"], "unmapped")
        key = f"{row['_id']['source']}:{row['_id']['status']}"
        plan_dist.setdefault(slug, {})[key] = row["n"]
    paid_active = await db.subscriptions.count_documents(
        {"source": "paddle", "status": {"$in": ["active", "trialing", "past_due"]}, "plan_id": {"$ne": None}}
    )
    storage = 0
    files = 0
    async for row in await db.files.aggregate(
        [
            {"$match": {"storage_status": {"$in": ["stored", "deleting"]}}},
            {"$group": {"_id": None, "b": {"$sum": "$original_size_bytes"}, "n": {"$sum": 1}}},
        ]
    ):
        storage, files = int(row["b"]), row["n"]
    usage: dict[str, int] = {}
    async for row in await db.usage_events.aggregate(
        [{"$match": {"occurred_at": {"$gte": since}}}, {"$group": {"_id": "$metric", "s": {"$sum": "$amount"}, "n": {"$sum": 1}}}]
    ):
        usage[row["_id"]] = int(row["s"]) if row["_id"] != "quota_denial" else int(row["n"])
    research: dict[str, int] = {}
    async for row in await db.research_jobs.aggregate(
        [{"$match": {"created_at": {"$gte": since}}}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}]
    ):
        research[row["_id"]] = row["n"]
    failed_jobs = await db.jobs.count_documents({"status": "failed", "updated_at": {"$gte": since}})
    provider = {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
    async for row in await db.provider_calls.aggregate(
        [
            {"$match": {"occurred_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": None,
                    "calls": {"$sum": 1},
                    "errors": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
                    "i": {"$sum": "$input_tokens"},
                    "o": {"$sum": "$output_tokens"},
                    "c": {"$sum": "$estimated_cost_usd"},
                }
            },
        ]
    ):
        provider = {
            "calls": row["calls"],
            "errors": row["errors"],
            "input_tokens": row["i"],
            "output_tokens": row["o"],
            "estimated_cost_usd": round(row["c"], 4),
        }
    billing: dict[str, int] = {}
    async for row in await db.billing_events.aggregate(
        [{"$match": {"received_at": {"$gte": since}}}, {"$group": {"_id": "$processing_status", "n": {"$sum": 1}}}]
    ):
        billing[row["_id"]] = row["n"]
    rejected = await db.webhook_rejections.count_documents({"received_at": {"$gte": since}})
    series = {
        "registrations": await _count_series("users", {}, "created_at", since),
        "uploads": await _count_series("files", {"storage_status": {"$in": ["stored", "deleting", "deleted"]}}, "created_at", since),
        "questions": await _count_series("usage_events", {"metric": "questions"}, "occurred_at", since),
        "research": await _count_series("research_jobs", {}, "created_at", since),
    }
    cost_series: dict[str, float] = {}
    async for row in await db.provider_calls.aggregate(
        [{"$match": {"occurred_at": {"$gte": since}}}, {"$group": {"_id": DAY_FMT, "c": {"$sum": "$estimated_cost_usd"}}}]
    ):
        cost_series[row["_id"]] = round(row["c"], 4)
    days_list = [(since + timedelta(days=i + 1)).strftime("%Y-%m-%d") for i in range(days)]
    return {
        "period_days": days,
        "users": {"total": total_users, "new": new_users, "active": len(active_ids)},
        "subscriptions": {"paid_active": paid_active, "by_plan": plan_dist},
        "mrr": await mrr_estimate(),
        "storage": {"bytes": storage, "files": files},
        "usage": {
            "uploads": usage.get("uploads", 0),
            "questions": usage.get("questions", 0),
            "research_reports": usage.get("research_reports", 0),
            "quota_denials": usage.get("quota_denial", 0),
        },
        "research_jobs": research,
        "failed_jobs": failed_jobs,
        "provider": {**provider, "cost_is_estimate": True},
        "billing_events": {**billing, "signature_rejections": rejected},
        "timeseries": [
            {"date": d, **{k: v.get(d, 0) for k, v in series.items()}, "estimated_cost_usd": cost_series.get(d, 0)} for d in days_list
        ],
    }


async def usage_report(
    *,
    since: datetime,
    until: datetime,
    account_id: Any = None,
    plan_id: Any = None,
    provider: str | None = None,
    model: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    db = get_db()
    accounts: list[Any] | None = None
    if plan_id is not None:
        accounts = await db.subscriptions.distinct("account_id", {"plan_id": plan_id})
    pmatch: dict[str, Any] = {"occurred_at": {"$gte": since, "$lt": until}}
    umatch: dict[str, Any] = {"occurred_at": {"$gte": since, "$lt": until}}
    if account_id is not None:
        pmatch["account_id"] = account_id
        umatch["account_id"] = account_id
    elif accounts is not None:
        pmatch["account_id"] = {"$in": accounts}
        umatch["account_id"] = {"$in": accounts}
    for k, v in (("provider", provider), ("model", model), ("operation", operation)):
        if v:
            pmatch[k] = v
    by_model = []
    async for row in await db.provider_calls.aggregate(
        [
            {"$match": pmatch},
            {
                "$group": {
                    "_id": {"provider": "$provider", "model": "$model", "operation": "$operation", "kind": "$kind"},
                    "calls": {"$sum": 1},
                    "errors": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
                    "retries": {"$sum": "$retries"},
                    "input_tokens": {"$sum": "$input_tokens"},
                    "output_tokens": {"$sum": "$output_tokens"},
                    "cost": {"$sum": "$estimated_cost_usd"},
                    "latency": {"$avg": "$latency_ms"},
                }
            },
            {"$sort": {"cost": -1}},
        ]
    ):
        by_model.append(
            {
                **row["_id"],
                "calls": row["calls"],
                "errors": row["errors"],
                "retries": row["retries"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "estimated_cost_usd": round(row["cost"], 6),
                "avg_latency_ms": round(row["latency"] or 0, 1),
            }
        )
    metrics: dict[str, Any] = {}
    async for row in await db.usage_events.aggregate(
        [{"$match": umatch}, {"$group": {"_id": "$metric", "s": {"$sum": "$amount"}, "n": {"$sum": 1}}}]
    ):
        metrics[row["_id"]] = {"total": row["s"], "events": row["n"]}
    top_accounts = []
    async for row in await db.provider_calls.aggregate(
        [
            {"$match": pmatch},
            {"$group": {"_id": "$account_id", "cost": {"$sum": "$estimated_cost_usd"}, "calls": {"$sum": 1}}},
            {"$sort": {"cost": -1}},
            {"$limit": 20},
        ]
    ):
        top_accounts.append(
            {"account_id": str(row["_id"]) if row["_id"] else None, "calls": row["calls"], "estimated_cost_usd": round(row["cost"], 6)}
        )
    return {
        "range": {"from": since.isoformat(), "to": until.isoformat()},
        "providers": by_model,
        "embedding_requests": sum(r["calls"] for r in by_model if r["kind"] == "embed"),
        "usage_metrics": metrics,
        "quota_denials": metrics.get("quota_denial", {}).get("events", 0),
        "top_accounts": top_accounts,
        "costs_are_estimates": True,
    }
