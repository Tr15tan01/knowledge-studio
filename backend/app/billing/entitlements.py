"""The single authoritative entitlement service.

Every backend permission or quota decision about plans goes through ``resolve()`` and the
helpers below. The frontend only displays what this returns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId

from app.billing import catalog, usage
from app.billing.periods import LIFETIME, LIVE, UsagePeriod, calendar_month, subscription_month, utc_day
from app.billing.schema import FEATURE_KEYS, Entitlements
from app.core.config import get_settings
from app.core.errors import Forbidden
from app.core.timeutil import ensure_utc, utcnow
from app.database.client import get_db

ENTITLED_STATUSES = ("active", "trialing")


@dataclass
class Resolved:
    account_id: Any
    plan_id: ObjectId
    plan_slug: str
    plan_name: str
    plan_version: int
    source: str  # "default" | "paddle"
    subscription_id: ObjectId | None
    subscription_status: str
    entitlements: Entitlements
    period: UsagePeriod
    adjustments: dict[str, int] = field(default_factory=dict)
    grants: list[dict[str, Any]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    def has_feature(self, key: str) -> bool:
        return bool(self.entitlements.features.get(key))

    def limit(self, metric: str) -> int | None:
        e = self.entitlements
        base = {
            "uploads": e.upload_limit_per_period,
            "questions": e.question_limit_per_period,
            "research_reports": e.research_report_limit_per_period,
            "storage_bytes": e.storage_limit_bytes,
            "concurrent_research": e.max_concurrent_research_jobs,
            "concurrent_jobs": e.max_concurrent_jobs,
            "compute_daily": e.daily_compute_budget,
            "compute_monthly": e.monthly_compute_budget,
            "cost_daily_micro_usd": int(e.max_daily_cost_usd * 1_000_000),
            "cost_monthly_micro_usd": int(e.max_monthly_cost_usd * 1_000_000),
        }.get(metric)
        if base is None:
            return None
        return max(0, base + self.adjustments.get(metric, 0))

    def period_for(self, metric: str, now: datetime | None = None) -> UsagePeriod:
        if metric == "storage_bytes":
            return LIFETIME
        if metric in ("concurrent_research", "concurrent_jobs"):
            return LIVE
        if metric in ("compute_daily", "cost_daily_micro_usd"):
            return utc_day(now or utcnow())
        return self.period


def _entitled(sub: dict[str, Any], now: datetime) -> bool:
    status = sub.get("status")
    if status in ENTITLED_STATUSES:
        end = ensure_utc(sub.get("current_period_end"))
        # Allow a small clock skew; Paddle sends renewal events at period boundaries.
        return end is None or end + timedelta(hours=12) > now
    if status == "past_due":
        since = ensure_utc(sub.get("past_due_since")) or now
        return since + timedelta(days=get_settings().billing_past_due_grace_days) > now
    return False


async def _default_assignment(account_id: Any) -> dict[str, Any]:
    db = get_db()
    sub = await db.subscriptions.find_one({"account_id": account_id, "source": "default"})
    if sub:
        plan = await db.plans.find_one({"_id": sub["plan_id"]})
        if plan:
            return sub
    plan = await catalog.default_plan()
    now = utcnow()
    await db.subscriptions.update_one(
        {"account_id": account_id, "source": "default"},
        {
            "$set": {"plan_id": plan["_id"], "plan_version": plan["current_version"], "status": "active", "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return await db.subscriptions.find_one({"account_id": account_id, "source": "default"})  # type: ignore[return-value]


async def resolve(account_id: Any, now: datetime | None = None) -> Resolved:
    now = now or utcnow()
    db = get_db()
    best: tuple[int, dict[str, Any], dict[str, Any]] | None = None
    async for sub in db.subscriptions.find({"account_id": account_id, "source": "paddle"}):
        if not _entitled(sub, now) or not sub.get("plan_id"):
            continue
        plan = await db.plans.find_one({"_id": sub["plan_id"]})
        if not plan:
            continue
        rank = int(plan.get("display_order", 0))
        if best is None or rank > best[0]:
            best = (rank, sub, plan)
    notices: list[str] = []
    if best:
        _, sub, plan = best
        source = "paddle"
        anchor = ensure_utc(sub.get("current_period_start"))
        period = (
            subscription_month(anchor, now, ensure_utc(sub.get("current_period_end")), str(sub["_id"])) if anchor else calendar_month(now)
        )
        if sub.get("status") == "past_due":
            notices.append("payment_past_due")
        if sub.get("scheduled_change", {}) and (sub.get("scheduled_change") or {}).get("action") == "cancel":
            notices.append("cancels_at_period_end")
    else:
        sub = await _default_assignment(account_id)
        plan = await db.plans.find_one({"_id": sub["plan_id"]})
        source = "default"
        period = calendar_month(now)
    if plan is None:
        raise RuntimeError("Plan catalog is missing the assigned plan; run `python -m app.cli setup-db`.")
    version = int(sub.get("plan_version") or plan["current_version"])
    pv = await db.plan_versions.find_one({"plan_id": plan["_id"], "version": version})
    if pv is None:  # pinned version missing: fail safe to the plan's current version
        pv = await db.plan_versions.find_one({"plan_id": plan["_id"], "version": plan["current_version"]})
        version = plan["current_version"]
    if pv is None:
        raise RuntimeError("Plan version document missing; the plan catalog is inconsistent.")
    ents = Entitlements.model_validate(pv["entitlements"])

    # Promotional grants and manual plan overrides (never reduce entitlements).
    grants = [
        g
        async for g in db.entitlement_grants.find(
            {"account_id": account_id, "revoked_at": None, "starts_at": {"$lte": now}, "expires_at": {"$gt": now}}
        )
    ]
    adjustments: dict[str, int] = {}
    if grants:
        merged = ents.model_dump()
        for g in grants:
            if g["kind"] == "adjustment":
                if g.get("period_key") == period.key:
                    adjustments[g["metric"]] = adjustments.get(g["metric"], 0) + int(g["delta"])
                continue
            overrides: dict[str, Any] = dict(g.get("overrides") or {})
            if g.get("plan_id"):
                gp = await db.plans.find_one({"_id": g["plan_id"]})
                if gp:
                    gpv = await db.plan_versions.find_one({"plan_id": gp["_id"], "version": gp["current_version"]})
                    if gpv:
                        overrides = {**gpv["entitlements"], **overrides}
            for k, v in overrides.items():
                if v is None:
                    continue
                if k == "features":
                    for fk, fv in v.items():
                        if fk in FEATURE_KEYS and fv:
                            merged["features"][fk] = True
                elif k in merged and isinstance(v, int | float):
                    merged[k] = max(merged[k], v)
        # Keep consistency constraints satisfied after merging.
        merged["monthly_compute_budget"] = max(merged["monthly_compute_budget"], merged["daily_compute_budget"])
        merged["storage_limit_bytes"] = max(merged["storage_limit_bytes"], merged["max_file_size_bytes"])
        ents = Entitlements.model_validate(merged)

    return Resolved(
        account_id=account_id,
        plan_id=plan["_id"],
        plan_slug=plan["slug"],
        plan_name=pv.get("name") or plan["name"],
        plan_version=version,
        source=source,
        subscription_id=sub.get("_id"),
        subscription_status=sub.get("status", "active"),
        entitlements=ents,
        period=period,
        adjustments=adjustments,
        grants=grants,
        notices=notices,
    )


def require_feature(r: Resolved, key: str, *, label: str | None = None) -> None:
    if not r.has_feature(key):
        raise Forbidden(
            f"{label or key.replace('_', ' ').capitalize()} is not included in your {r.plan_name} plan.",
            code="feature_not_in_plan",
            details={"feature": key, "upgrade_path": "/pricing"},
        )


async def reserve_metric(
    r: Resolved,
    metric: str,
    amount: int,
    *,
    idempotency_key: str | None = None,
    ttl_seconds: int = 1800,
    meta: dict[str, Any] | None = None,
) -> usage.Reservation:
    period = r.period_for(metric)
    return await usage.reserve(
        account_id=r.account_id,
        metric=metric,
        amount=amount,
        limit=r.limit(metric),
        period=period,
        idempotency_key=idempotency_key or f"{metric}:{uuid.uuid4()}",
        ttl=timedelta(seconds=ttl_seconds),
        meta=meta,
        reset_at=period.end if period.key not in ("lifetime", "live") else None,
    )


async def usage_summary(r: Resolved) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric in ("uploads", "questions", "research_reports", "storage_bytes", "concurrent_research", "compute_daily", "compute_monthly"):
        p = r.period_for(metric)
        snap = await usage.usage_snapshot(r.account_id, metric, p)
        out[metric] = {
            **snap,
            "limit": r.limit(metric),
            "period_start": p.start.isoformat() if p.key not in ("lifetime", "live") else None,
            "resets_at": p.end.isoformat() if p.key not in ("lifetime", "live") else None,
        }
    return out
