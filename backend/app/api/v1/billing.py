"""Billing: checkout, subscription status, portal, plan changes and the Paddle webhook."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.v1.me import entitlements_out
from app.auth.deps import Auth
from app.billing import paddle
from app.billing.entitlements import resolve
from app.billing.webhooks import handle_webhook
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.database.ids import oid
from app.schemas.common import APIModel, iso
from app.security import ratelimit
from app.services import audit

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutIn(APIModel):
    plan_id: str
    interval: Literal["month", "year"]


class ChangePlanIn(APIModel):
    plan_id: str
    interval: Literal["month", "year"]
    confirm: bool = False


def _sub_out(sub: dict[str, Any] | None) -> dict[str, Any] | None:
    if not sub:
        return None
    sc = sub.get("scheduled_change") or {}
    return {
        "id": str(sub["_id"]),
        "status": sub.get("status"),
        "billing_interval": sub.get("billing_interval"),
        "current_period_start": iso(sub.get("current_period_start")),
        "current_period_end": iso(sub.get("current_period_end")),
        "cancels_at_period_end": sc.get("action") == "cancel",
        "scheduled_change": {"action": sc.get("action"), "effective_at": sc.get("effective_at")} if sc else None,
        "past_due_since": iso(sub.get("past_due_since")),
        "plan_version": sub.get("plan_version"),
        "unmapped_price": sub.get("unmapped_price", False),
    }


async def _active_paddle_sub(account_id: Any) -> dict[str, Any] | None:
    return await get_db().subscriptions.find_one(
        {"account_id": account_id, "source": "paddle", "status": {"$in": ["active", "trialing", "past_due", "paused"]}},
        sort=[("updated_at", -1)],
    )


@router.get("/subscription")
async def subscription(auth: Auth) -> dict[str, Any]:
    s = get_settings()
    r = await resolve(auth.account_id)
    sub = await _active_paddle_sub(auth.account_id)
    customer = await get_db().billing_customers.find_one({"account_id": auth.account_id})
    events = [
        {
            "action": e["action"],
            "at": iso(e["created_at"]),
            "details": {k: v for k, v in (e.get("details") or {}).items() if k in ("status", "price_id")},
        }
        async for e in get_db()
        .audit_events.find({"target_id": str(auth.account_id), "action": {"$regex": "^billing\\."}})
        .sort("created_at", -1)
        .limit(20)
    ]
    return {
        "billing_enabled": s.paddle_enabled,
        "environment": s.paddle_environment if s.paddle_enabled else None,
        "client_token": s.paddle_client_token if s.paddle_enabled else None,
        "entitlements": entitlements_out(r),
        "subscription": _sub_out(sub),
        "has_billing_portal": bool(customer and customer.get("paddle_customer_id")) and s.paddle_enabled,
        "history": events,
        "note": "Premium access is granted only after Paddle confirms payment. It can take a few seconds after checkout.",
    }


async def _mapping(plan_id: str, interval: str) -> tuple[dict[str, Any], dict[str, Any]]:
    db = get_db()
    plan = await db.plans.find_one({"_id": oid(plan_id, what="plan"), "is_active": True, "is_public": True, "archived_at": None})
    if not plan:
        raise NotFound("This plan isn't available.")
    m = await db.price_mappings.find_one({"plan_id": plan["_id"], "interval": interval, "active": True})
    if not m:
        raise ValidationFailed("Checkout isn't configured for this plan and billing interval yet.", code="price_not_mapped")
    if m.get("environment") and m["environment"] != get_settings().paddle_environment:
        raise ValidationFailed("This price is mapped for a different Paddle environment.", code="price_environment_mismatch")
    return plan, m


@router.post("/checkout")
async def checkout(body: CheckoutIn, auth: Auth) -> dict[str, Any]:
    """Create a Paddle transaction for the mapped price. The browser only opens it; it cannot set the amount."""
    await ratelimit.hit("checkout", str(auth.user_id), limit=10, window_seconds=600)
    plan, m = await _mapping(body.plan_id, body.interval)
    if await _active_paddle_sub(auth.account_id):
        raise Conflict("You already have a subscription. Use 'Change plan' or the billing portal instead.", code="already_subscribed")
    db = get_db()
    customer = await db.billing_customers.find_one({"account_id": auth.account_id})
    customer_id = (customer or {}).get("paddle_customer_id")
    if not customer_id:
        try:
            existing = await paddle.list_customers_by_email(auth.user["email"])
            customer_id = (
                existing[0]["id"] if existing else (await paddle.create_customer(auth.user["email"], auth.user.get("display_name")))["id"]
            )
        except Exception:  # noqa: BLE001 - checkout can still collect customer details itself
            customer_id = None
        if customer_id:
            await db.billing_customers.update_one(
                {"account_id": auth.account_id}, {"$set": {"paddle_customer_id": customer_id, "updated_at": utcnow()}}, upsert=True
            )
    txn = await paddle.create_transaction(
        price_id=m["paddle_price_id"],
        customer_id=customer_id,
        custom_data={"account_id": str(auth.account_id), "plan_id": str(plan["_id"]), "interval": body.interval},
    )
    await audit.record(
        "billing.checkout_started",
        actor_id=auth.user_id,
        target_type="user",
        target_id=auth.account_id,
        details={"plan": plan["slug"], "interval": body.interval, "price_id": m["paddle_price_id"]},
    )
    s = get_settings()
    return {
        "transaction_id": txn["id"],
        "client_token": s.paddle_client_token,
        "environment": s.paddle_environment,
        "success_path": "/app/billing?checkout=complete",
    }


@router.post("/portal")
async def portal(auth: Auth) -> dict[str, Any]:
    customer = await get_db().billing_customers.find_one({"account_id": auth.account_id})
    if not customer or not customer.get("paddle_customer_id"):
        raise NotFound("No billing account exists yet.")
    sub = await _active_paddle_sub(auth.account_id)
    session = await paddle.portal_session(customer["paddle_customer_id"], [sub["paddle_subscription_id"]] if sub else [])
    return {"url": session["urls"]["general"]["overview"]}


@router.post("/change-plan")
async def change_plan(body: ChangePlanIn, auth: Auth) -> dict[str, Any]:
    """Upgrade/downgrade between paid prices. Always previewed; applied only with explicit confirmation."""
    sub = await _active_paddle_sub(auth.account_id)
    if not sub:
        raise NotFound("You don't have an active subscription. Start a checkout instead.")
    plan, m = await _mapping(body.plan_id, body.interval)
    if m["paddle_price_id"] == sub.get("paddle_price_id"):
        raise ValidationFailed("You're already on this plan and billing interval.")
    if not body.confirm:
        preview = await paddle.preview_subscription_change(sub["paddle_subscription_id"], m["paddle_price_id"])
        it = preview.get("immediate_transaction") or {}
        totals = (it.get("details") or {}).get("totals") or {}
        return {
            "status": "preview",
            "currency": preview.get("currency_code"),
            "immediate_charge_minor": totals.get("grand_total"),
            "next_billed_at": preview.get("next_billed_at"),
            "plan": plan["name"],
            "interval": body.interval,
            "message": "Review the prorated charge, then confirm to apply the change.",
        }
    await paddle.change_subscription(sub["paddle_subscription_id"], m["paddle_price_id"])
    await audit.record(
        "billing.plan_change_requested",
        actor_id=auth.user_id,
        target_type="user",
        target_id=auth.account_id,
        details={"to_plan": plan["slug"], "interval": body.interval},
    )
    return {"status": "requested", "message": "Change submitted to Paddle. Your plan updates as soon as Paddle confirms it."}


@router.post("/cancel")
async def cancel(auth: Auth) -> dict[str, Any]:
    sub = await _active_paddle_sub(auth.account_id)
    if not sub:
        raise NotFound("No active subscription.")
    await paddle.cancel_at_period_end(sub["paddle_subscription_id"])
    await audit.record("billing.cancel_requested", actor_id=auth.user_id, target_type="user", target_id=auth.account_id)
    return {
        "status": "requested",
        "message": "Your subscription will end at the close of the current billing period. Your files are never deleted.",
    }


@router.post("/resume")
async def resume(auth: Auth) -> dict[str, Any]:
    sub = await _active_paddle_sub(auth.account_id)
    if not sub or (sub.get("scheduled_change") or {}).get("action") != "cancel":
        raise ValidationFailed("There is no scheduled cancellation to undo.")
    await paddle.remove_scheduled_change(sub["paddle_subscription_id"])
    await audit.record("billing.resume_requested", actor_id=auth.user_id, target_type="user", target_id=auth.account_id)
    return {"status": "requested"}


@router.post("/webhooks/paddle", include_in_schema=False)
async def paddle_webhook(request: Request) -> JSONResponse:
    if not get_settings().paddle_enabled:
        return JSONResponse({"status": "billing_disabled"}, status_code=404)
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        return JSONResponse({"status": "too_large"}, status_code=413)
    status, body = await handle_webhook(raw, request.headers.get("paddle-signature"))
    return JSONResponse(body, status_code=status)
