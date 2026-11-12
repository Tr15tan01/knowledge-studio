"""Administration API.

Authorization is enforced on every endpoint by backend dependencies:
* ``AdminAuth`` (ADMIN or SUPER_ADMIN): operations — metrics, users, usage, jobs, health, audit.
* ``SuperAdminAuth``: plans and pricing, Paddle price mappings, roles, policy, settings, re-indexing.
Sensitive writes additionally require recent re-authentication and are audited.
No endpoint returns secrets, password hashes, session tokens or private document content.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import Field

from app.admin.metrics import overview as overview_metrics
from app.admin.metrics import usage_report
from app.api.v1.me import entitlements_out
from app.api.v1.plans import public_plan
from app.auth.deps import AdminAuth, AuthContext, SuperAdminAuth, require_recent_auth
from app.auth.service import request_password_reset, revoke_all_sessions
from app.billing import catalog, paddle
from app.billing.entitlements import resolve, usage_summary
from app.billing.schema import ADJUSTABLE_METRICS, EntitlementOverrides, PlanCreate, PlanUpdate
from app.billing.storage_accounting import storage_summary
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.timeutil import ensure_utc, parse_iso, utcnow
from app.database.client import get_db
from app.database.ids import oid
from app.jobs import queue
from app.observability.health import readiness
from app.schemas.common import APIModel, iso, paging
from app.services import audit
from app.services.system_settings import (
    QuotaPolicy,
    RuntimeSettings,
    get_quota_policy,
    get_runtime_settings,
    runtime_defaults,
    runtime_settings_meta,
    save_quota_policy,
    save_runtime_settings,
)
from app.storage import available_backends

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_RETRYABLE_TYPES = {
    "ingest.file",
    "graph.extract",
    "file.delete",
    "reindex.embed",
    "account.export",
    "account.delete",
    "maintenance.reconcile",
}


class Reason(APIModel):
    reason: str = Field(min_length=3, max_length=500)


def _actor(auth: AuthContext) -> ObjectId:
    return auth.user_id


# --- Overview -------------------------------------------------------------------------------


@router.get("/overview")
async def overview(auth: AdminAuth, days: int = Query(30, ge=7, le=365)) -> dict[str, Any]:
    return await overview_metrics(days)


# --- Plans & pricing ------------------------------------------------------------------------


async def _plan_admin_out(p: dict[str, Any]) -> dict[str, Any]:
    v = p["version_doc"]
    maps = [
        {
            "id": str(m["_id"]),
            "interval": m["interval"],
            "paddle_price_id": m["paddle_price_id"],
            "active": m.get("active", False),
            "verified": m.get("verified", False),
            "verified_price": m.get("verified_price"),
            "environment": m.get("environment"),
            "updated_at": iso(m.get("updated_at")),
        }
        async for m in get_db().price_mappings.find({"plan_id": p["_id"]})
    ]
    return {
        "id": str(p["_id"]),
        "slug": p["slug"],
        "name": v.get("name") or p["name"],
        "description": v.get("description", ""),
        "is_active": p.get("is_active", True),
        "is_public": p.get("is_public", False),
        "is_default": p.get("is_default", False),
        "display_order": p.get("display_order", 0),
        "archived_at": iso(p.get("archived_at")),
        "current_version": p["current_version"],
        "pricing": v["pricing"],
        "marketing": v.get("marketing", {}),
        "entitlements": v["entitlements"],
        "subscribers_by_version": {str(k): n for k, n in (await catalog.subscriber_counts(p["_id"])).items()},
        "price_mappings": maps,
        "updated_at": iso(p.get("updated_at")),
    }


@router.get("/plans")
async def list_plans(auth: AdminAuth, include_archived: bool = True) -> dict[str, Any]:
    return {"items": [await _plan_admin_out(p) for p in await catalog.list_plans(include_archived=include_archived)]}


@router.post("/plans", status_code=201)
async def create_plan(body: PlanCreate, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    return await _plan_admin_out(await catalog.create_plan(body, _actor(auth)))


@router.patch("/plans/{plan_id}")
async def update_plan(plan_id: str, body: PlanUpdate, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    return await _plan_admin_out(await catalog.update_plan(plan_id, body, _actor(auth)))


@router.get("/plans/{plan_id}/history")
async def plan_history(plan_id: str, auth: AdminAuth) -> dict[str, Any]:
    items = []
    for v in await catalog.plan_history(plan_id):
        items.append(
            {
                "version": v["version"],
                "change_note": v.get("change_note"),
                "apply_policy": v.get("apply_policy"),
                "created_at": iso(v["created_at"]),
                "created_by": str(v["created_by"]) if v.get("created_by") else "system",
                "pricing": v["pricing"],
                "entitlements": v["entitlements"],
                "name": v.get("name"),
            }
        )
    return {"items": items}


class MigrateIn(APIModel):
    from_version: int | None = Field(default=None, ge=1)
    to_version: int = Field(ge=1)
    account_ids: list[str] | None = Field(default=None, max_length=10_000)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/plans/{plan_id}/migrate")
async def migrate(plan_id: str, body: MigrateIn, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    n = await catalog.migrate_subscribers(
        plan_id,
        from_version=body.from_version,
        to_version=body.to_version,
        account_ids=body.account_ids,
        actor_id=_actor(auth),
        reason=body.reason,
    )
    return {"migrated": n}


class ArchiveIn(Reason):
    grandfather: bool = False
    migrate_to_plan_id: str | None = None


@router.post("/plans/{plan_id}/archive")
async def archive(plan_id: str, body: ArchiveIn, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    return await _plan_admin_out(
        await catalog.archive_plan(
            plan_id, actor_id=_actor(auth), reason=body.reason, migrate_to_plan_id=body.migrate_to_plan_id, grandfather=body.grandfather
        )
    )


@router.post("/plans/{plan_id}/default")
async def make_default(plan_id: str, auth: SuperAdminAuth) -> dict[str, str]:
    require_recent_auth(auth)
    await catalog.set_default_plan(plan_id, _actor(auth))
    return {"status": "updated"}


class ReorderIn(APIModel):
    order: list[str] = Field(min_length=1, max_length=50)


@router.post("/plans/reorder")
async def reorder(body: ReorderIn, auth: SuperAdminAuth) -> dict[str, str]:
    await catalog.reorder_plans(body.order, _actor(auth))
    return {"status": "updated"}


@router.get("/plans-preview")
async def pricing_preview(auth: AdminAuth, include_hidden: bool = False) -> dict[str, Any]:
    plans = await catalog.list_plans(public_only=not include_hidden)
    out = []
    for p in plans:
        maps = {m["interval"]: m async for m in get_db().price_mappings.find({"plan_id": p["_id"], "active": True})}
        out.append({**public_plan(p, maps), "is_public": p.get("is_public")})
    return {"plans": out}


# --- Paddle price mappings ------------------------------------------------------------------


class PriceMappingIn(APIModel):
    plan_id: str
    interval: Literal["month", "year"]
    paddle_price_id: str = Field(pattern=r"^pri_[a-z0-9]{10,40}$")
    reason: str = Field(min_length=3, max_length=500)


async def _verify_price(price_id: str, plan: dict[str, Any], interval: str) -> dict[str, Any]:
    """Validate the Paddle price exists, is active, and matches currency and interval."""
    price = await paddle.get_price(price_id)
    if price.get("status") != "active":
        raise ValidationFailed("This Paddle price is not active.", code="price_inactive")
    cycle = price.get("billing_cycle") or {}
    if cycle.get("interval") != interval or int(cycle.get("frequency") or 0) != 1:
        raise ValidationFailed(
            f"This Paddle price bills every {cycle.get('frequency')} {cycle.get('interval')}, not every 1 {interval}.",
            code="price_interval_mismatch",
        )
    unit = price.get("unit_price") or {}
    pv = plan["version_doc"]["pricing"]
    if unit.get("currency_code") != pv.get("currency"):
        raise ValidationFailed(
            f"Price currency {unit.get('currency_code')} does not match the plan currency {pv.get('currency')}.",
            code="price_currency_mismatch",
        )
    amount = int(unit.get("amount") or 0)
    display = pv.get("monthly_display_price_minor" if interval == "month" else "annual_display_price_minor")
    product = await paddle.get_product(price["product_id"]) if price.get("product_id") else {}
    if product and product.get("status") != "active":
        raise ValidationFailed("The Paddle product for this price is not active.", code="product_inactive")
    return {
        "unit_amount_minor": amount,
        "currency": unit.get("currency_code"),
        "product_id": price.get("product_id"),
        "product_name": product.get("name"),
        "display_matches": display == amount,
        "checked_at": utcnow().isoformat(),
    }


@router.get("/price-mappings")
async def list_mappings(auth: AdminAuth) -> dict[str, Any]:
    names = {p["_id"]: p["name"] async for p in get_db().plans.find({}, projection={"name": 1})}
    return {
        "items": [
            {
                "id": str(m["_id"]),
                "plan_id": str(m["plan_id"]),
                "plan_name": names.get(m["plan_id"]),
                "interval": m["interval"],
                "paddle_price_id": m["paddle_price_id"],
                "active": m.get("active"),
                "verified": m.get("verified"),
                "verified_price": m.get("verified_price"),
                "environment": m.get("environment"),
                "updated_at": iso(m.get("updated_at")),
                "updated_by": str(m.get("updated_by")) if m.get("updated_by") else None,
            }
            async for m in get_db().price_mappings.find({}).sort("plan_id", 1)
        ],
        "paddle_environment": get_settings().paddle_environment,
        "paddle_enabled": get_settings().paddle_enabled,
    }


@router.put("/price-mappings")
async def upsert_mapping(body: PriceMappingIn, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    s = get_settings()
    plan = await catalog.get_plan(body.plan_id)
    if plan.get("archived_at"):
        raise ValidationFailed("Archived plans cannot receive new price mappings.")
    verified_price = None
    if s.paddle_enabled and s.paddle_api_key.get_secret_value():
        verified_price = await _verify_price(body.paddle_price_id, plan, body.interval)
    elif s.is_production:
        raise ValidationFailed("Paddle must be enabled and reachable to map prices in production.", code="price_verification_required")
    db = get_db()
    clash = await db.price_mappings.find_one(
        {"paddle_price_id": body.paddle_price_id, "$or": [{"plan_id": {"$ne": plan["_id"]}}, {"interval": {"$ne": body.interval}}]}
    )
    if clash:
        raise Conflict("This Paddle price is already mapped to another plan or interval.")
    previous = await db.price_mappings.find_one({"plan_id": plan["_id"], "interval": body.interval})
    now = utcnow()
    await db.price_mappings.update_one(
        {"plan_id": plan["_id"], "interval": body.interval},
        {
            "$set": {
                "paddle_price_id": body.paddle_price_id,
                "active": True,
                "verified": verified_price is not None,
                "verified_price": verified_price,
                "environment": s.paddle_environment,
                "updated_at": now,
                "updated_by": _actor(auth),
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    await audit.record(
        "billing.price_mapping_changed",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="plan",
        target_id=plan["_id"],
        reason=body.reason,
        details={
            "interval": body.interval,
            "from": (previous or {}).get("paddle_price_id"),
            "to": body.paddle_price_id,
            "verified": verified_price is not None,
            "note": "Existing subscriptions keep their current Paddle price; only new checkouts use this mapping.",
        },
    )
    return {
        "status": "saved",
        "verified": verified_price is not None,
        "verified_price": verified_price,
        "warning": None
        if verified_price
        else "Saved without Paddle verification (billing disabled in this environment). Checkout stays unavailable until Paddle is enabled.",
    }


@router.post("/price-mappings/{mapping_id}/deactivate")
async def deactivate_mapping(mapping_id: str, body: Reason, auth: SuperAdminAuth) -> dict[str, str]:
    require_recent_auth(auth)
    m = await get_db().price_mappings.find_one_and_update(
        {"_id": oid(mapping_id, what="mapping")}, {"$set": {"active": False, "updated_at": utcnow(), "updated_by": _actor(auth)}}
    )
    if not m:
        raise NotFound("Mapping not found.")
    await audit.record(
        "billing.price_mapping_deactivated",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="plan",
        target_id=m["plan_id"],
        reason=body.reason,
        details={"price_id": m["paddle_price_id"]},
    )
    return {"status": "deactivated"}


@router.post("/price-mappings/{mapping_id}/verify")
async def verify_mapping(mapping_id: str, auth: SuperAdminAuth) -> dict[str, Any]:
    m = await get_db().price_mappings.find_one({"_id": oid(mapping_id, what="mapping")})
    if not m:
        raise NotFound("Mapping not found.")
    plan = await catalog.get_plan(m["plan_id"])
    vp = await _verify_price(m["paddle_price_id"], plan, m["interval"])
    await get_db().price_mappings.update_one({"_id": m["_id"]}, {"$set": {"verified": True, "verified_price": vp, "updated_at": utcnow()}})
    await audit.record(
        "billing.price_mapping_verified",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="plan",
        target_id=m["plan_id"],
        details={"price_id": m["paddle_price_id"]},
    )
    return {"verified": True, "verified_price": vp}


# --- Users ----------------------------------------------------------------------------------


def _user_row(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(u["_id"]),
        "email": u["email"],
        "display_name": u.get("display_name"),
        "role": u.get("role"),
        "status": u.get("status"),
        "email_verified": bool(u.get("email_verified_at")),
        "created_at": iso(u.get("created_at")),
        "last_login_at": iso(u.get("last_login_at")),
    }


@router.get("/users")
async def list_users(
    auth: AdminAuth,
    q: str | None = Query(None, max_length=200),
    status: str | None = None,
    role: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt: dict[str, Any] = {}
    if q:
        flt["$or"] = [{"email_normalized": {"$regex": re.escape(q.lower())}}, {"display_name": {"$regex": re.escape(q), "$options": "i"}}]
        if ObjectId.is_valid(q):
            flt["$or"].append({"_id": ObjectId(q)})
    if status in ("active", "suspended", "deleting"):
        flt["status"] = status
    if role in ("USER", "ADMIN", "SUPER_ADMIN"):
        flt["role"] = role
    db = get_db()
    total = await db.users.count_documents(flt)
    rows = [u async for u in db.users.find(flt, projection={"password_hash": 0}).sort("created_at", -1).skip(skip).limit(page_size)]
    out = []
    for u in rows:
        r = await resolve(u["_id"])
        out.append({**_user_row(u), "plan": r.plan_name, "plan_source": r.source, "subscription_status": r.subscription_status})
    return {"items": out, "total": total, "page": page, "page_size": page_size}


async def _target_user(user_id: str) -> dict[str, Any]:
    u = await get_db().users.find_one({"_id": oid(user_id, what="user")}, projection={"password_hash": 0})
    if not u:
        raise NotFound("User not found.")
    return u


@router.get("/users/{user_id}")
async def get_user(user_id: str, auth: AdminAuth) -> dict[str, Any]:
    u = await _target_user(user_id)
    r = await resolve(u["_id"])
    db = get_db()
    subs = [
        {
            "id": str(s["_id"]),
            "source": s.get("source"),
            "status": s.get("status"),
            "plan_id": str(s["plan_id"]) if s.get("plan_id") else None,
            "plan_version": s.get("plan_version"),
            "billing_interval": s.get("billing_interval"),
            "paddle_subscription_id": s.get("paddle_subscription_id"),
            "current_period_end": iso(s.get("current_period_end")),
            "unmapped_price": s.get("unmapped_price", False),
        }
        async for s in db.subscriptions.find({"account_id": u["_id"]})
    ]
    grants = [
        {
            "id": str(g["_id"]),
            "kind": g["kind"],
            "metric": g.get("metric"),
            "delta": g.get("delta"),
            "plan_id": str(g["plan_id"]) if g.get("plan_id") else None,
            "overrides": g.get("overrides"),
            "starts_at": iso(g.get("starts_at")),
            "expires_at": iso(g.get("expires_at")),
            "revoked_at": iso(g.get("revoked_at")),
            "reason": g.get("reason"),
        }
        async for g in db.entitlement_grants.find({"account_id": u["_id"]}).sort("_id", -1).limit(50)
    ]
    history = [
        {
            "action": a["action"],
            "actor_id": str(a["actor_id"]) if a.get("actor_id") else None,
            "actor_type": a.get("actor_type"),
            "reason": a.get("reason"),
            "at": iso(a["created_at"]),
        }
        async for a in db.audit_events.find({"$or": [{"target_id": str(u["_id"])}, {"actor_id": u["_id"]}]})
        .sort("created_at", -1)
        .limit(50)
    ]
    sessions = await db.sessions.count_documents({"user_id": u["_id"], "revoked_at": None, "expires_at": {"$gt": utcnow()}})
    content = {
        "files": await db.files.count_documents({"owner_id": u["_id"], "storage_status": "stored"}),
        "reports": await db.reports.count_documents({"user_id": u["_id"]}),
    }
    await audit.record("admin.user_viewed", actor_id=_actor(auth), actor_type="admin", target_type="user", target_id=u["_id"])
    return {
        **_user_row(u),
        "entitlements": entitlements_out(r),
        "usage": await usage_summary(r),
        "storage": await storage_summary(r),
        "subscriptions": subs,
        "grants": grants,
        "audit": history,
        "active_sessions": sessions,
        "content_counts": content,
        "privacy_note": "Document contents are not accessible from the admin panel.",
    }


@router.post("/users/{user_id}/suspend")
async def suspend(user_id: str, body: Reason, auth: AdminAuth) -> dict[str, str]:
    u = await _target_user(user_id)
    if u["_id"] == auth.user_id:
        raise ValidationFailed("You can't suspend your own account.")
    if u.get("role") in ("ADMIN", "SUPER_ADMIN") and not auth.has_role("SUPER_ADMIN"):
        raise Forbidden("Only a super administrator can suspend administrators.")
    if (
        u.get("role") == "SUPER_ADMIN"
        and await get_db().users.count_documents({"role": "SUPER_ADMIN", "status": "active", "_id": {"$ne": u["_id"]}}) == 0
    ):
        raise ValidationFailed("You can't suspend the last active super administrator.")
    await get_db().users.update_one(
        {"_id": u["_id"]}, {"$set": {"status": "suspended", "suspended_at": utcnow(), "suspension_reason": body.reason}}
    )
    n = await revoke_all_sessions(u["_id"], reason="suspended")
    await audit.record(
        "admin.user_suspended",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="user",
        target_id=u["_id"],
        reason=body.reason,
        details={"sessions_revoked": n},
    )
    return {"status": "suspended"}


@router.post("/users/{user_id}/restore")
async def restore(user_id: str, body: Reason, auth: AdminAuth) -> dict[str, str]:
    u = await _target_user(user_id)
    if u["status"] != "suspended":
        raise ValidationFailed("Only suspended accounts can be restored.")
    await get_db().users.update_one(
        {"_id": u["_id"]}, {"$set": {"status": "active"}, "$unset": {"suspended_at": "", "suspension_reason": ""}}
    )
    await audit.record(
        "admin.user_restored", actor_id=_actor(auth), actor_type="admin", target_type="user", target_id=u["_id"], reason=body.reason
    )
    return {"status": "active"}


class AdjustmentIn(Reason):
    metric: Literal["uploads", "questions", "research_reports"]
    delta: int = Field(ge=-10_000, le=10_000)


@router.post("/users/{user_id}/quota-adjustments", status_code=201)
async def quota_adjustment(user_id: str, body: AdjustmentIn, auth: AdminAuth) -> dict[str, Any]:
    u = await _target_user(user_id)
    assert body.metric in ADJUSTABLE_METRICS
    r = await resolve(u["_id"])
    res = await get_db().entitlement_grants.insert_one(
        {
            "account_id": u["_id"],
            "kind": "adjustment",
            "metric": body.metric,
            "delta": body.delta,
            "period_key": r.period.key,
            "starts_at": utcnow(),
            "expires_at": r.period.end,
            "revoked_at": None,
            "reason": body.reason,
            "created_by": _actor(auth),
            "created_at": utcnow(),
        }
    )
    await audit.record(
        "admin.quota_adjusted",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="user",
        target_id=u["_id"],
        reason=body.reason,
        details={"metric": body.metric, "delta": body.delta, "period": r.period.key},
    )
    return {"id": str(res.inserted_id), "applies_until": r.period.end.isoformat()}


class GrantIn(Reason):
    plan_id: str | None = None
    overrides: EntitlementOverrides | None = None
    expires_at: datetime
    public_note: str | None = Field(default=None, max_length=200)


@router.post("/users/{user_id}/grants", status_code=201)
async def grant(user_id: str, body: GrantIn, auth: AdminAuth) -> dict[str, Any]:
    u = await _target_user(user_id)
    exp = ensure_utc(body.expires_at)
    if exp is None or exp <= utcnow():
        raise ValidationFailed("The expiration date must be in the future.")
    if exp > utcnow() + timedelta(days=366):
        raise ValidationFailed("Promotional grants can last at most one year.")
    if not body.plan_id and not body.overrides:
        raise ValidationFailed("Choose a plan to grant or specific entitlement overrides.")
    plan_oid = None
    if body.plan_id:
        plan = await catalog.get_plan(body.plan_id)
        plan_oid = plan["_id"]
    res = await get_db().entitlement_grants.insert_one(
        {
            "account_id": u["_id"],
            "kind": "promo",
            "plan_id": plan_oid,
            "overrides": body.overrides.model_dump(exclude_none=True) if body.overrides else None,
            "starts_at": utcnow(),
            "expires_at": exp,
            "revoked_at": None,
            "reason": body.reason,
            "public_note": body.public_note,
            "created_by": _actor(auth),
            "created_at": utcnow(),
        }
    )
    await audit.record(
        "admin.entitlement_granted",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="user",
        target_id=u["_id"],
        reason=body.reason,
        details={
            "plan_id": body.plan_id,
            "overrides": body.overrides.model_dump(exclude_none=True) if body.overrides else None,
            "expires_at": exp.isoformat(),
        },
    )
    return {"id": str(res.inserted_id)}


@router.post("/users/{user_id}/grants/{grant_id}/revoke")
async def revoke_grant(user_id: str, grant_id: str, body: Reason, auth: AdminAuth) -> dict[str, str]:
    u = await _target_user(user_id)
    res = await get_db().entitlement_grants.update_one(
        {"_id": oid(grant_id, what="grant"), "account_id": u["_id"], "revoked_at": None},
        {"$set": {"revoked_at": utcnow(), "revoked_by": _actor(auth)}},
    )
    if not res.modified_count:
        raise NotFound("Active grant not found.")
    await audit.record(
        "admin.entitlement_revoked",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="user",
        target_id=u["_id"],
        reason=body.reason,
        details={"grant_id": grant_id},
    )
    return {"status": "revoked"}


@router.post("/users/{user_id}/recovery")
async def trigger_recovery(user_id: str, body: Reason, auth: AdminAuth) -> dict[str, Any]:
    u = await _target_user(user_id)
    await request_password_reset(u["email"])
    await audit.record(
        "admin.recovery_triggered", actor_id=_actor(auth), actor_type="admin", target_type="user", target_id=u["_id"], reason=body.reason
    )
    return {
        "status": "sent" if get_settings().email_provider == "smtp" else "not_delivered",
        "email_delivery_configured": get_settings().email_provider == "smtp",
    }


class RoleIn(Reason):
    role: Literal["USER", "ADMIN", "SUPER_ADMIN"]


@router.post("/users/{user_id}/role")
async def set_role(user_id: str, body: RoleIn, auth: SuperAdminAuth) -> dict[str, str]:
    require_recent_auth(auth)
    u = await _target_user(user_id)
    if u.get("role") == body.role:
        return {"status": "unchanged"}
    if u.get("role") == "SUPER_ADMIN" and body.role != "SUPER_ADMIN":
        others = await get_db().users.count_documents({"role": "SUPER_ADMIN", "status": "active", "_id": {"$ne": u["_id"]}})
        if others == 0:
            raise ValidationFailed("This is the last active super administrator; promote another account first.", code="last_super_admin")
    if body.role != "USER" and u["status"] != "active":
        raise ValidationFailed("Only active accounts can be given administrative roles.")
    await get_db().users.update_one({"_id": u["_id"]}, {"$set": {"role": body.role, "updated_at": utcnow()}})
    if u["_id"] != auth.user_id:
        await revoke_all_sessions(u["_id"], reason="role_changed")
    await audit.record(
        "admin.role_changed",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="user",
        target_id=u["_id"],
        reason=body.reason,
        details={"from": u.get("role"), "to": body.role},
    )
    return {"status": "updated"}


# --- Usage & costs --------------------------------------------------------------------------


def _range(from_: str | None, to: str | None) -> tuple[datetime, datetime]:
    until = parse_iso(to) or utcnow()
    since = parse_iso(from_) or (until - timedelta(days=30))
    if since >= until or (until - since).days > 400:
        raise ValidationFailed("Invalid date range (max 400 days).")
    return since, until


@router.get("/usage")
async def usage(
    auth: AdminAuth,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    user_id: str | None = None,
    plan_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    since, until = _range(from_, to)
    return await usage_report(
        since=since,
        until=until,
        account_id=oid(user_id) if user_id else None,
        plan_id=oid(plan_id) if plan_id else None,
        provider=provider,
        model=model,
        operation=operation,
    )


@router.get("/usage/export.csv")
async def usage_csv(auth: AdminAuth, from_: str | None = Query(None, alias="from"), to: str | None = None) -> Response:
    """Per-account aggregates. Emails and content are excluded (data minimisation)."""
    since, until = _range(from_, to)
    db = get_db()
    rows: dict[str, dict[str, Any]] = {}
    async for r in await db.usage_events.aggregate(
        [
            {"$match": {"occurred_at": {"$gte": since, "$lt": until}}},
            {"$group": {"_id": {"a": "$account_id", "m": "$metric"}, "s": {"$sum": "$amount"}, "n": {"$sum": 1}}},
        ]
    ):
        a = str(r["_id"]["a"])
        rows.setdefault(a, {})[r["_id"]["m"]] = r["n"] if r["_id"]["m"] == "quota_denial" else r["s"]
    async for r in await db.provider_calls.aggregate(
        [
            {"$match": {"occurred_at": {"$gte": since, "$lt": until}}},
            {
                "$group": {
                    "_id": "$account_id",
                    "i": {"$sum": "$input_tokens"},
                    "o": {"$sum": "$output_tokens"},
                    "c": {"$sum": "$estimated_cost_usd"},
                    "e": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
                }
            },
        ]
    ):
        d = rows.setdefault(str(r["_id"]), {})
        d.update({"input_tokens": r["i"], "output_tokens": r["o"], "estimated_cost_usd": round(r["c"], 6), "provider_errors": r["e"]})
    buf = io.StringIO()
    cols = [
        "account_id",
        "uploads",
        "storage_bytes",
        "questions",
        "research_reports",
        "quota_denial",
        "input_tokens",
        "output_tokens",
        "provider_errors",
        "estimated_cost_usd",
    ]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for acct, d in rows.items():
        if acct == "__platform__":
            continue
        w.writerow({"account_id": acct, **{c: d.get(c, 0) for c in cols[1:]}})
    await audit.record(
        "admin.usage_exported",
        actor_id=_actor(auth),
        actor_type="admin",
        details={"from": since.isoformat(), "to": until.isoformat(), "rows": len(rows)},
    )
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="usage.csv"'})


class PricesIn(APIModel):
    prices: dict[str, dict[str, float]]
    reason: str = Field(min_length=3, max_length=300)


@router.get("/model-prices")
async def get_prices(auth: AdminAuth) -> dict[str, Any]:
    from app.providers.telemetry import price_table

    return {"prices": await price_table(), "unit": "USD per 1M tokens", "note": "Used only for cost estimates."}


@router.put("/model-prices")
async def put_prices(body: PricesIn, auth: SuperAdminAuth) -> dict[str, str]:
    for model, p in body.prices.items():
        if len(model) > 100 or set(p) - {"input", "output"} or any(not 0 <= v <= 1000 for v in p.values()):
            raise ValidationFailed(f"Invalid price entry for {model}.")
    await get_db().system_settings.update_one(
        {"_id": "model_prices"}, {"$set": {"prices": body.prices, "updated_at": utcnow(), "updated_by": _actor(auth)}}, upsert=True
    )
    await audit.record(
        "admin.model_prices_changed", actor_id=_actor(auth), actor_type="admin", reason=body.reason, details={"models": list(body.prices)}
    )
    return {"status": "saved"}


@router.get("/quota-policy")
async def quota_policy(auth: AdminAuth) -> dict[str, Any]:
    return (await get_quota_policy()).model_dump()


class PolicyIn(QuotaPolicy):
    reason: str = Field(min_length=3, max_length=300)


@router.put("/quota-policy")
async def put_quota_policy(body: PolicyIn, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    policy = QuotaPolicy.model_validate(body.model_dump(exclude={"reason"}))
    before = (await get_quota_policy()).model_dump()
    await save_quota_policy(policy, _actor(auth))
    await audit.record(
        "admin.quota_policy_changed",
        actor_id=_actor(auth),
        actor_type="admin",
        reason=body.reason,
        details={"before": before, "after": policy.model_dump()},
    )
    return policy.model_dump()


# --- Settings -------------------------------------------------------------------------------


async def _settings_out() -> dict[str, Any]:
    meta = await runtime_settings_meta()
    held = [
        {"backend": row["_id"] or get_settings().storage_backend, "files": row["n"], "bytes": int(row["b"])}
        async for row in await get_db().files.aggregate(
            [
                {"$match": {"storage_status": {"$in": ["uploading", "stored", "deleting"]}, "original_purged_at": None}},
                {"$group": {"_id": "$storage_backend", "n": {"$sum": 1}, "b": {"$sum": {"$ifNull": ["$original_size_bytes", 0]}}}},
                {"$sort": {"_id": 1}},
            ]
        )
    ]
    return {
        "settings": (await get_runtime_settings()).model_dump(),
        "defaults": runtime_defaults().model_dump(),
        "storage_backends": available_backends(),
        "files_held": held,
        "updated_at": iso(meta["updated_at"]),
        "updated_by": str(meta["updated_by"]) if meta["updated_by"] else None,
    }


@router.get("/settings")
async def get_settings_(auth: AdminAuth) -> dict[str, Any]:
    return await _settings_out()


class SettingsIn(RuntimeSettings):
    reason: str = Field(min_length=3, max_length=300)


@router.put("/settings")
async def put_settings(body: SettingsIn, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    value = RuntimeSettings.model_validate(body.model_dump(exclude={"reason"}))
    if value.storage_backend not in available_backends():
        raise ValidationFailed("S3 storage isn't configured on this server. Choose the server disk or MongoDB.")
    before = (await get_runtime_settings()).model_dump()
    after = value.model_dump()
    # A list, not a mapping keyed by setting name: audit redaction hides values under keys
    # that look secret, and "context_max_tokens" does.
    changes = [{"setting": k, "before": before[k], "after": after[k]} for k in sorted(after) if after[k] != before[k]]
    await save_runtime_settings(value, _actor(auth))
    await audit.record(
        "admin.settings_changed", actor_id=_actor(auth), actor_type="admin", reason=body.reason, details={"changes": changes}
    )
    return await _settings_out()


# --- Jobs, health, configuration ------------------------------------------------------------


def _job_row(j: dict[str, Any]) -> dict[str, Any]:
    err = j.get("error") or {}
    return {
        "id": str(j["_id"]),
        "type": j["type"],
        "status": j["status"],
        "attempts": j.get("attempts"),
        "max_attempts": j.get("max_attempts"),
        "account_id": str(j["account_id"]) if j.get("account_id") else None,
        "progress": j.get("progress"),
        "error": {"code": err.get("code"), "message": err.get("message"), "retryable": err.get("retryable")} if err else None,
        "retryable_by_admin": j["type"] in ADMIN_RETRYABLE_TYPES and j["status"] == "failed",
        "lease_expires_at": iso(j.get("lease_expires_at")),
        "created_at": iso(j.get("created_at")),
        "updated_at": iso(j.get("updated_at")),
    }


@router.get("/jobs")
async def list_jobs(
    auth: AdminAuth, status: str | None = None, type: str | None = None, stuck: bool = False, page: int = 1, page_size: int = 30
) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt: dict[str, Any] = {}
    if status:
        flt["status"] = status
    if type:
        flt["type"] = type
    if stuck:
        flt.update({"status": "running", "lease_expires_at": {"$lt": utcnow()}})
    db = get_db()
    total = await db.jobs.count_documents(flt)
    items = [
        _job_row(j)
        async for j in db.jobs.find(flt, projection={"payload": 0, "result": 0}).sort("updated_at", -1).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "stats": await queue.queue_stats()}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, body: Reason, auth: AdminAuth) -> dict[str, str]:
    j = await get_db().jobs.find_one({"_id": oid(job_id, what="job")})
    if not j:
        raise NotFound("Job not found.")
    if j["type"] not in ADMIN_RETRYABLE_TYPES:
        raise ValidationFailed(
            "This job type can't be retried by an administrator (its quota holds were settled). Ask the user to start it again."
        )
    if not await queue.retry_failed(j["_id"]):
        raise ValidationFailed("Only failed jobs can be retried.")
    if j["type"] == "ingest.file":
        await get_db().files.update_one(
            {"_id": ObjectId(j["payload"]["file_id"]), "storage_status": "stored"},
            {"$set": {"processing_status": "queued", "processing_error": None}},
        )
    await audit.record(
        "admin.job_retried",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="job",
        target_id=j["_id"],
        reason=body.reason,
        details={"type": j["type"]},
    )
    return {"status": "queued"}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, body: Reason, auth: AdminAuth) -> dict[str, str]:
    j = await get_db().jobs.find_one({"_id": oid(job_id, what="job")})
    if not j:
        raise NotFound("Job not found.")
    if j["type"] in ("account.delete", "file.delete"):
        raise ValidationFailed("Deletion jobs can't be cancelled once requested.")
    result = await queue.request_cancel(j["_id"])
    if j["type"] == "research.run" and result == "cancelled":
        from app.research.agent import settle

        run = await get_db().research_jobs.find_one({"job_id": j["_id"]})
        if run:
            await settle(run, outcome="cancelled")
            await get_db().research_jobs.update_one({"_id": run["_id"]}, {"$set": {"status": "cancelled", "phase": "cancelled"}})
    await audit.record(
        "admin.job_cancelled",
        actor_id=_actor(auth),
        actor_type="admin",
        target_type="job",
        target_id=j["_id"],
        reason=body.reason,
        details={"result": result},
    )
    return {"status": result}


@router.get("/health")
async def health(auth: AdminAuth) -> dict[str, Any]:
    from app.embeddings import get_embedding_provider
    from app.providers.llm import get_llm

    report = await readiness(deep=True)
    providers: dict[str, Any] = {}
    try:
        providers["llm"] = dict(zip(("ok", "detail"), await get_llm("answer").health(), strict=True))
    except Exception as exc:  # noqa: BLE001
        providers["llm"] = {"ok": False, "detail": type(exc).__name__}
    try:
        meta = get_embedding_provider().get_model_metadata()
        providers["embeddings"] = {"ok": True, "detail": f"{meta.model} · {meta.dimensions} dims · space {meta.space_id}"}
    except Exception as exc:  # noqa: BLE001
        providers["embeddings"] = {"ok": False, "detail": type(exc).__name__}
    ok, detail = await paddle.health()
    db = get_db()
    since = utcnow() - timedelta(hours=24)
    webhooks = {
        "processed_24h": await db.billing_events.count_documents({"received_at": {"$gte": since}, "processing_status": "processed"}),
        "failed_24h": await db.billing_events.count_documents({"received_at": {"$gte": since}, "processing_status": "failed"}),
        "rejected_signatures_24h": await db.webhook_rejections.count_documents({"received_at": {"$gte": since}}),
        "last_received_at": iso(((await db.billing_events.find_one({}, sort=[("received_at", -1)])) or {}).get("received_at")),
    }
    workers = [
        {"id": w["_id"], "seen_at": iso(w["seen_at"]), "concurrency": w.get("concurrency")} async for w in db.worker_heartbeats.find({})
    ]
    return {
        **report,
        "providers": providers,
        "paddle": {"ok": ok, "detail": detail},
        "webhooks": webhooks,
        "workers": workers,
        "queue": await queue.queue_stats(),
    }


@router.get("/config")
async def config_status(auth: AdminAuth) -> dict[str, Any]:
    """Configuration presence only. Secret values are never returned."""
    s = get_settings()
    rt = await get_runtime_settings()

    def flag(v: Any) -> str:
        val = v.get_secret_value() if hasattr(v, "get_secret_value") else v
        return "configured" if val else "missing"

    return {
        "environment": s.app_env.value,
        "integrations": {
            "ai_providers": {
                "answers": f"{s.llm_provider} · {s.llm_model}",
                "research": f"{s.research_llm_provider} · {s.research_llm_model}",
                "extraction": f"{s.extraction_llm_provider} · {s.extraction_llm_model}",
                "api_key": "configured" if s.effective_llm_api_key else "missing",
                "reranker": s.reranker_provider,
            },
            "embeddings": {
                "provider": s.embedding_provider,
                "model": s.gemini_embedding_model,
                "dimensions": s.gemini_embedding_dimensions,
                "space_id": s.embedding_space_id,
                "api_key": flag(s.gemini_api_key),
                "vector_field": s.vector_field,
            },
            "search": {
                "vector_backend": s.vector_search_backend,
                "keyword_backend": s.keyword_search_backend,
                "vector_index": s.atlas_vector_index_name,
                "text_index": s.atlas_search_index_name,
            },
            "file_storage": {
                "backend": rt.storage_backend,
                "keep_original_files": rt.keep_original_files,
                "s3_bucket": s.s3_bucket or "not used",
            },
            "email": {
                "provider": s.email_provider,
                "smtp_host": "configured" if s.smtp_host else "missing",
                "smtp_password": flag(s.smtp_password),
            },
            "paddle": {
                "enabled": s.paddle_enabled,
                "environment": s.paddle_environment,
                "api_key": flag(s.paddle_api_key),
                "webhook_secret": flag(s.paddle_webhook_secret),
                "client_token": "configured" if s.paddle_client_token else "missing",
            },
            "google_oauth": {
                "enabled": s.google_auth_enabled,
                "client_id": "configured" if s.google_client_id else "missing",
                "client_secret": flag(s.google_client_secret),
            },
            "monitoring": {"sentry": flag(s.sentry_dsn), "log_format": "json" if s.log_json else "text"},
            "queue": {
                "backend": s.queue_backend,
                "worker_concurrency": s.worker_concurrency,
                "lease_seconds": s.job_lease_seconds,
                "max_attempts": s.job_max_attempts,
            },
        },
        "problems": s.validate_for_startup(),
        "note": "Secrets are managed through environment variables on the server and cannot be viewed or changed here.",
    }


# --- Audit & billing events -----------------------------------------------------------------


@router.get("/audit")
async def audit_log(
    auth: AdminAuth,
    action: str | None = None,
    actor_id: str | None = None,
    target_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size, 200)
    flt: dict[str, Any] = {}
    if action:
        flt["action"] = {"$regex": "^" + re.escape(action)}
    if actor_id:
        flt["actor_id"] = oid(actor_id)
    if target_id:
        flt["target_id"] = target_id
    db = get_db()
    total = await db.audit_events.count_documents(flt)
    items = [
        {
            "id": str(a["_id"]),
            "action": a["action"],
            "actor_id": str(a["actor_id"]) if a.get("actor_id") else None,
            "actor_type": a.get("actor_type"),
            "target_type": a.get("target_type"),
            "target_id": a.get("target_id"),
            "reason": a.get("reason"),
            "details": a.get("details"),
            "request_id": a.get("request_id"),
            "created_at": iso(a["created_at"]),
        }
        async for a in db.audit_events.find(flt).sort("created_at", -1).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "immutable": True}


@router.get("/billing/events")
async def billing_events(auth: AdminAuth, status: str | None = None, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt = {"processing_status": status} if status else {}
    db = get_db()
    total = await db.billing_events.count_documents(flt)
    items = [
        {
            "event_id": e["event_id"],
            "event_type": e["event_type"],
            "status": e["processing_status"],
            "occurred_at": iso(e.get("occurred_at")),
            "received_at": iso(e["received_at"]),
            "attempts": e.get("attempts"),
            "error": e.get("error"),
            "subscription_id": ((e.get("payload") or {}).get("data") or {}).get("id"),
        }
        async for e in db.billing_events.find(flt).sort("received_at", -1).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --- Re-indexing ----------------------------------------------------------------------------


class ReindexIn(APIModel):
    provider: Literal["gemini"] = "gemini"
    model: str = Field(min_length=3, max_length=100)
    dimensions: int = Field(ge=128, le=3072)
    vector_field: str = Field(min_length=3, max_length=40)
    note: str = Field(min_length=3, max_length=300)


@router.post("/reindex", status_code=202)
async def start_reindex(body: ReindexIn, auth: SuperAdminAuth) -> dict[str, Any]:
    require_recent_auth(auth)
    from app.retrieval.reindex import start_migration

    m = await start_migration(
        provider=body.provider,
        model=body.model,
        dimensions=body.dimensions,
        vector_field=body.vector_field,
        actor_id=_actor(auth),
        note=body.note,
    )
    return {"id": str(m["_id"]), "job_id": str(m["job_id"]), "target": m["target"], "total_chunks": m["total_chunks"]}


@router.get("/reindex")
async def list_reindex(auth: AdminAuth) -> dict[str, Any]:
    from app.retrieval.reindex import coverage

    out = []
    async for m in get_db().reindex_migrations.find({}).sort("created_at", -1).limit(20):
        out.append(
            {
                "id": str(m["_id"]),
                "status": m["status"],
                "note": m.get("note"),
                "created_at": iso(m["created_at"]),
                **(await coverage(m["_id"])),
            }
        )
    return {"items": out, "active_space": get_settings().embedding_space_id, "active_vector_field": get_settings().vector_field}


@router.post("/reprocess-files", status_code=202)
async def reprocess(body: Reason, auth: SuperAdminAuth) -> dict[str, int]:
    """Re-chunk/re-embed files whose chunking version or embedding space differs from the active configuration.

    Files whose original was deleted after indexing can be embedded again from their stored
    passages, but not chunked again.
    """
    require_recent_auth(auth)
    s = get_settings()
    n = 0
    async for f in get_db().files.find(
        {
            "storage_status": "stored",
            "processing_status": "indexed",
            "$or": [
                {"chunking_version": {"$ne": s.chunking_version}, "original_purged_at": None},
                {"embedding_space": {"$ne": s.embedding_space_id}},
            ],
        },
        projection={"owner_id": 1, "workspace_id": 1},
    ):
        await queue.enqueue(
            "ingest.file",
            {"file_id": str(f["_id"])},
            account_id=f["owner_id"],
            workspace_id=f["workspace_id"],
            priority=-5,
            dedupe_key=f"reprocess:{f['_id']}:{s.chunking_version}:{s.embedding_space_id}",
        )
        n += 1
    await audit.record("admin.reprocess_files", actor_id=_actor(auth), actor_type="admin", reason=body.reason, details={"files": n})
    return {"queued": n}
