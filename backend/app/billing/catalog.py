"""Plan catalog with immutable versions.

* ``plans`` holds the plan identity and its *current* version pointer.
* ``plan_versions`` holds immutable snapshots (entitlements, display pricing, marketing).
* Subscriptions/assignments pin a ``plan_version``; editing a plan never silently changes
  existing subscribers unless the administrator explicitly chooses ``all_subscribers`` or a
  cohort, or later runs an explicit migration.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.billing.schema import Entitlements, PlanCreate, PlanUpdate, seed_plans
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.database.ids import maybe_oid, oid
from app.services import audit

log = logging.getLogger("app.billing.catalog")

SIGNIFICANT_FIELDS = ("entitlements", "pricing")


async def seed_catalog(actor: str = "system") -> list[str]:
    """Create the initial Free/Premium plans if the catalog is empty. Idempotent."""
    db = get_db()
    created: list[str] = []
    if await db.plans.count_documents({}) > 0:
        return created
    for i, p in enumerate(seed_plans()):
        plan_id = await _insert_plan(p, actor_id=None, actor_type="system", is_default=(i == 0), note="Initial seed")
        created.append(str(plan_id))
    return created


PRICE_ID_RE = re.compile(r"^pri_[a-z0-9]{10,40}$")


async def bootstrap_price_mappings() -> dict[str, str]:
    """Map PADDLE_PRICE_ID_PREMIUM_MONTHLY/YEARLY to the seeded Premium plan, once.

    Only fills an empty slot. Mappings managed in the admin panel are never overwritten, and
    bootstrapped mappings start unverified until an administrator verifies them with Paddle.
    """
    from app.core.config import get_settings
    from app.services import audit

    s = get_settings()
    db = get_db()
    wanted = {"month": s.paddle_price_id_premium_monthly.strip(), "year": s.paddle_price_id_premium_yearly.strip()}
    if not any(wanted.values()):
        return {}
    plan = await db.plans.find_one({"slug": "premium"})
    if not plan:
        return {"premium": "plan not found"}
    out: dict[str, str] = {}
    now = utcnow()
    for interval, price_id in wanted.items():
        if not price_id:
            continue
        if not PRICE_ID_RE.match(price_id):
            out[interval] = "invalid price id format (expected pri_...)"
            continue
        existing = await db.price_mappings.find_one({"plan_id": plan["_id"], "interval": interval})
        if existing:
            out[interval] = "kept existing mapping" if existing["paddle_price_id"] != price_id else "already mapped"
            continue
        if await db.price_mappings.find_one({"paddle_price_id": price_id}):
            out[interval] = "price id already mapped elsewhere"
            continue
        await db.price_mappings.insert_one(
            {
                "plan_id": plan["_id"],
                "interval": interval,
                "paddle_price_id": price_id,
                "active": True,
                "verified": False,
                "verified_price": None,
                "environment": s.paddle_environment,
                "source": "env_bootstrap",
                "created_at": now,
                "updated_at": now,
                "updated_by": None,
            }
        )
        await audit.record(
            "billing.price_mapping_changed",
            actor_type="system",
            target_type="plan",
            target_id=plan["_id"],
            reason="bootstrap from environment",
            details={"interval": interval, "from": None, "to": price_id, "verified": False},
        )
        out[interval] = "mapped (unverified)"
    return out


async def _insert_plan(p: PlanCreate, *, actor_id: ObjectId | None, actor_type: str, is_default: bool, note: str) -> ObjectId:
    db = get_db()
    now = utcnow()
    try:
        res = await db.plans.insert_one(
            {
                "slug": p.slug,
                "name": p.name,
                "description": p.description,
                "is_active": True,
                "is_public": p.is_public,
                "is_default": is_default,
                "display_order": p.display_order,
                "current_version": 1,
                "archived_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    except DuplicateKeyError as exc:
        raise Conflict(f"A plan with slug '{p.slug}' already exists.") from exc
    await db.plan_versions.insert_one(
        {
            "plan_id": res.inserted_id,
            "version": 1,
            "name": p.name,
            "description": p.description,
            "pricing": p.pricing.model_dump(),
            "marketing": p.marketing.model_dump(),
            "entitlements": p.entitlements.model_dump(),
            "change_note": note,
            "apply_policy": "new_subscribers_only",
            "created_by": actor_id,
            "created_at": now,
        }
    )
    await audit.record(
        "plan.created",
        actor_id=actor_id,
        actor_type=actor_type,
        target_type="plan",
        target_id=res.inserted_id,
        details={"slug": p.slug, "version": 1},
    )
    return res.inserted_id


async def create_plan(p: PlanCreate, actor_id: ObjectId) -> dict[str, Any]:
    plan_id = await _insert_plan(p, actor_id=actor_id, actor_type="admin", is_default=False, note="Created")
    return await get_plan(plan_id)


async def get_plan(plan_id: ObjectId | str, *, version: int | None = None) -> dict[str, Any]:
    db = get_db()
    plan = await db.plans.find_one({"_id": oid(plan_id, what="plan")})
    if not plan:
        raise NotFound("Plan not found.")
    v = version or plan["current_version"]
    pv = await db.plan_versions.find_one({"plan_id": plan["_id"], "version": v})
    if not pv:
        raise NotFound("Plan version not found.")
    return {**plan, "version_doc": pv}


async def get_plan_by_slug(slug: str) -> dict[str, Any] | None:
    plan = await get_db().plans.find_one({"slug": slug})
    return await get_plan(plan["_id"]) if plan else None


async def default_plan() -> dict[str, Any]:
    plan = await get_db().plans.find_one({"is_default": True, "archived_at": None})
    if not plan:
        await seed_catalog()
        plan = await get_db().plans.find_one({"is_default": True, "archived_at": None})
    if not plan:
        raise NotFound("No default plan is configured.")
    return await get_plan(plan["_id"])


async def list_plans(*, public_only: bool = False, include_archived: bool = False) -> list[dict[str, Any]]:
    q: dict[str, Any] = {}
    if public_only:
        q.update({"is_public": True, "is_active": True})
    if not include_archived:
        q["archived_at"] = None
    out = []
    async for plan in get_db().plans.find(q).sort([("display_order", 1), ("_id", 1)]):
        pv = await get_db().plan_versions.find_one({"plan_id": plan["_id"], "version": plan["current_version"]})
        out.append({**plan, "version_doc": pv})
    return out


async def plan_history(plan_id: str) -> list[dict[str, Any]]:
    return [v async for v in get_db().plan_versions.find({"plan_id": oid(plan_id, what="plan")}).sort("version", -1).limit(200)]


async def subscriber_counts(plan_id: ObjectId) -> dict[int, int]:
    counts: dict[int, int] = {}
    async for row in await get_db().subscriptions.aggregate(
        [
            {"$match": {"plan_id": plan_id, "status": {"$in": ["active", "trialing", "past_due", "paused"]}}},
            {"$group": {"_id": "$plan_version", "n": {"$sum": 1}}},
        ]
    ):
        counts[int(row["_id"] or 0)] = row["n"]
    return counts


async def update_plan(plan_id: str, upd: PlanUpdate, actor_id: ObjectId) -> dict[str, Any]:
    """Create a new version and apply it according to the explicit policy."""
    db = get_db()
    current = await get_plan(plan_id)
    pv = current["version_doc"]
    significant = any(getattr(upd, f) is not None for f in SIGNIFICANT_FIELDS)
    if significant and not upd.confirm:
        raise ValidationFailed(
            "This change affects pricing or entitlements. Review the impact and confirm to publish.",
            code="confirmation_required",
            details={"subscribers_by_version": {str(k): v for k, v in (await subscriber_counts(current["_id"])).items()}},
        )
    if upd.apply_policy == "cohort" and not upd.cohort_account_ids:
        raise ValidationFailed("Select at least one account for a cohort rollout.")

    new_version = int(current["current_version"]) + 1
    ents = upd.entitlements.model_dump() if upd.entitlements else pv["entitlements"]
    Entitlements.model_validate(ents)  # re-validate stored data as well
    now = utcnow()
    version_doc = {
        "plan_id": current["_id"],
        "version": new_version,
        "name": upd.name or pv["name"],
        "description": upd.description if upd.description is not None else pv.get("description", ""),
        "pricing": upd.pricing.model_dump() if upd.pricing else pv["pricing"],
        "marketing": upd.marketing.model_dump() if upd.marketing else pv.get("marketing", {}),
        "entitlements": ents,
        "change_note": upd.change_note,
        "apply_policy": upd.apply_policy,
        "cohort_size": len(upd.cohort_account_ids),
        "created_by": actor_id,
        "created_at": now,
        "previous_version": current["current_version"],
    }
    try:
        await db.plan_versions.insert_one(version_doc)
    except DuplicateKeyError as exc:
        raise Conflict("The plan was modified concurrently. Reload and try again.") from exc
    plan_set: dict[str, Any] = {
        "current_version": new_version,
        "updated_at": now,
        "name": version_doc["name"],
        "description": version_doc["description"],
    }
    for f in ("is_public", "is_active", "display_order"):
        if getattr(upd, f) is not None:
            plan_set[f] = getattr(upd, f)
    res = await db.plans.update_one({"_id": current["_id"], "current_version": current["current_version"]}, {"$set": plan_set})
    if res.modified_count != 1:
        await db.plan_versions.delete_one({"plan_id": current["_id"], "version": new_version})
        raise Conflict("The plan was modified concurrently. Reload and try again.")

    migrated = 0
    if upd.apply_policy == "all_subscribers":
        migrated = await _migrate(current["_id"], None, new_version, None)
    elif upd.apply_policy == "cohort":
        ids = [maybe_oid(a) for a in upd.cohort_account_ids]
        migrated = await _migrate(current["_id"], None, new_version, [i for i in ids if i])

    diff = _diff(pv, version_doc)
    await audit.record(
        "plan.version_published",
        actor_id=actor_id,
        actor_type="admin",
        target_type="plan",
        target_id=current["_id"],
        reason=upd.change_note,
        details={
            "from_version": current["current_version"],
            "to_version": new_version,
            "policy": upd.apply_policy,
            "migrated": migrated,
            "changes": diff,
        },
    )
    return await get_plan(current["_id"])


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section in ("pricing", "entitlements", "marketing"):
        o, n = old.get(section) or {}, new.get(section) or {}
        for k in set(o) | set(n):
            if o.get(k) != n.get(k):
                out[f"{section}.{k}"] = {"from": o.get(k), "to": n.get(k)}
    for k in ("name", "description"):
        if old.get(k) != new.get(k):
            out[k] = {"from": old.get(k), "to": new.get(k)}
    return out


async def _migrate(plan_id: ObjectId, from_version: int | None, to_version: int, account_ids: list[ObjectId] | None) -> int:
    q: dict[str, Any] = {"plan_id": plan_id}
    if from_version is not None:
        q["plan_version"] = from_version
    if account_ids is not None:
        q["account_id"] = {"$in": account_ids}
    res = await get_db().subscriptions.update_many(q, {"$set": {"plan_version": to_version, "plan_version_changed_at": utcnow()}})
    return res.modified_count


async def migrate_subscribers(
    plan_id: str, *, from_version: int | None, to_version: int, account_ids: list[str] | None, actor_id: ObjectId, reason: str
) -> int:
    plan = await get_plan(plan_id, version=to_version)  # validates the target version exists
    ids = None if account_ids is None else [i for i in (maybe_oid(a) for a in account_ids) if i]
    n = await _migrate(plan["_id"], from_version, to_version, ids)
    await audit.record(
        "plan.subscribers_migrated",
        actor_id=actor_id,
        actor_type="admin",
        target_type="plan",
        target_id=plan["_id"],
        reason=reason,
        details={"from_version": from_version, "to_version": to_version, "count": n, "cohort": account_ids is not None},
    )
    return n


async def archive_plan(
    plan_id: str, *, actor_id: ObjectId, reason: str, migrate_to_plan_id: str | None, grandfather: bool
) -> dict[str, Any]:
    db = get_db()
    plan = await get_plan(plan_id)
    if plan.get("is_default"):
        raise ValidationFailed("The default plan cannot be archived. Choose another default first.")
    active = sum((await subscriber_counts(plan["_id"])).values())
    if active and not grandfather and not migrate_to_plan_id:
        raise ValidationFailed(
            f"{active} active subscription(s) use this plan. Choose to grandfather them or define a migration target.",
            code="archive_requires_policy",
            details={"active_subscriptions": active},
        )
    if migrate_to_plan_id:
        # Only *entitlement assignments* without a paid Paddle price are migrated automatically.
        # Paid subscriptions must be moved in Paddle explicitly; they are never silently repriced.
        target = await get_plan(migrate_to_plan_id)
        await db.subscriptions.update_many(
            {"plan_id": plan["_id"], "source": {"$ne": "paddle"}},
            {"$set": {"plan_id": target["_id"], "plan_version": target["current_version"], "updated_at": utcnow()}},
        )
    await db.plans.update_one(
        {"_id": plan["_id"]},
        {
            "$set": {
                "archived_at": utcnow(),
                "is_public": False,
                "is_active": False,
                "archive_policy": "grandfather" if grandfather else "migrate",
            }
        },
    )
    await audit.record(
        "plan.archived",
        actor_id=actor_id,
        actor_type="admin",
        target_type="plan",
        target_id=plan["_id"],
        reason=reason,
        details={"active_subscriptions": active, "grandfather": grandfather, "migrate_to": migrate_to_plan_id},
    )
    return await get_plan(plan["_id"])


async def set_default_plan(plan_id: str, actor_id: ObjectId) -> None:
    db = get_db()
    plan = await get_plan(plan_id)
    if plan.get("archived_at"):
        raise ValidationFailed("An archived plan cannot be the default.")
    await db.plans.update_many({"is_default": True}, {"$set": {"is_default": False}})
    await db.plans.update_one({"_id": plan["_id"]}, {"$set": {"is_default": True}})
    await audit.record("plan.default_changed", actor_id=actor_id, actor_type="admin", target_type="plan", target_id=plan["_id"])


async def reorder_plans(order: list[str], actor_id: ObjectId) -> None:
    for i, pid in enumerate(order):
        await get_db().plans.update_one({"_id": oid(pid, what="plan")}, {"$set": {"display_order": (i + 1) * 10}})
    await audit.record("plan.reordered", actor_id=actor_id, actor_type="admin", target_type="plan", details={"order": order})
