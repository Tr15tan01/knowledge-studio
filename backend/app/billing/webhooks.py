"""Paddle webhook processing: verified, idempotent, order-safe.

* Signatures are verified over the exact raw body before anything is parsed.
* ``billing_events.event_id`` is unique: duplicates are acknowledged without re-applying.
  Events whose processing previously failed are retried when Paddle redelivers them.
* Subscription state only moves forward in time: an event older than the last applied
  event for that subscription is recorded as ``stale`` and ignored. When an API key is
  configured we re-fetch the authoritative subscription from Paddle instead of trusting the
  payload alone.
* Premium entitlements come only from verified subscription state mapped through the
  admin-managed price mappings; checkout redirects never grant access.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.billing import paddle
from app.core.config import get_settings
from app.core.timeutil import parse_iso, utcnow
from app.database.client import get_db
from app.services import audit

log = logging.getLogger("app.billing.webhooks")

SUBSCRIPTION_EVENTS = {
    "subscription.created",
    "subscription.updated",
    "subscription.activated",
    "subscription.canceled",
    "subscription.past_due",
    "subscription.paused",
    "subscription.resumed",
    "subscription.trialing",
    "subscription.imported",
}
KNOWN_STATUSES = {"active", "trialing", "past_due", "paused", "canceled"}


class WebhookResult(dict):  # type: ignore[type-arg]
    pass


def _sanitize(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep what we need for audit/debugging; drop addresses and payment details."""
    data = dict(payload.get("data") or {})
    for k in ("address", "payments", "billing_details", "business"):
        data.pop(k, None)
    return {**{k: payload.get(k) for k in ("event_id", "event_type", "occurred_at", "notification_id")}, "data": data}


async def handle_webhook(raw: bytes, signature: str | None) -> tuple[int, dict[str, Any]]:
    s = get_settings()
    try:
        paddle.verify_signature(
            raw, signature, s.paddle_webhook_secret.get_secret_value(), tolerance_seconds=s.paddle_webhook_tolerance_seconds
        )
    except paddle.SignatureError as exc:
        log.warning("paddle_webhook_rejected", extra={"reason": str(exc)})
        await get_db().webhook_rejections.insert_one({"provider": "paddle", "reason": str(exc), "received_at": utcnow()})
        return 401, {"status": "rejected"}
    try:
        payload = json.loads(raw)
    except ValueError:
        return 400, {"status": "invalid_json"}
    event_id, event_type = payload.get("event_id"), payload.get("event_type")
    if not event_id or not event_type:
        return 400, {"status": "invalid_event"}
    db = get_db()
    doc = {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": parse_iso(payload.get("occurred_at")),
        "payload": _sanitize(payload),
        "received_at": utcnow(),
        "processing_status": "received",
        "attempts": 0,
    }
    try:
        await db.billing_events.insert_one(doc)
    except DuplicateKeyError:
        existing = await db.billing_events.find_one({"event_id": event_id})
        if existing and existing["processing_status"] in ("processed", "stale", "ignored"):
            return 200, {"status": "duplicate"}
        # Previous attempt failed or is in-flight: claim it for reprocessing atomically.
        claimed = await db.billing_events.find_one_and_update(
            {"event_id": event_id, "processing_status": {"$in": ["failed", "received"]}},
            {"$set": {"processing_status": "received", "retried_at": utcnow()}},
        )
        if not claimed:
            return 200, {"status": "duplicate"}
    await db.billing_events.update_one({"event_id": event_id}, {"$inc": {"attempts": 1}})
    try:
        status = await _process(payload)
    except Exception as exc:  # noqa: BLE001
        log.exception("paddle_webhook_processing_failed", extra={"event_type": event_type})
        await db.billing_events.update_one(
            {"event_id": event_id}, {"$set": {"processing_status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}}
        )
        return 500, {"status": "processing_failed"}  # Paddle will retry delivery
    await db.billing_events.update_one({"event_id": event_id}, {"$set": {"processing_status": status, "processed_at": utcnow()}})
    return 200, {"status": status}


async def _process(payload: dict[str, Any]) -> str:
    et = payload["event_type"]
    if et in SUBSCRIPTION_EVENTS:
        return await apply_subscription(payload["data"], occurred_at=parse_iso(payload.get("occurred_at")), event_id=payload["event_id"])
    if et == "customer.created" or et == "customer.updated":
        return "processed"
    if et in ("transaction.completed", "transaction.paid", "transaction.payment_failed", "transaction.past_due"):
        await audit.record(
            f"billing.{et}",
            actor_type="webhook",
            target_type="paddle_transaction",
            target_id=(payload.get("data") or {}).get("id"),
            details={
                "status": (payload.get("data") or {}).get("status"),
                "subscription_id": (payload.get("data") or {}).get("subscription_id"),
            },
        )
        return "processed"
    return "ignored"


async def _account_for(data: dict[str, Any]) -> ObjectId | None:
    db = get_db()
    cd = data.get("custom_data") or {}
    acct = cd.get("account_id")
    if acct and ObjectId.is_valid(acct) and await db.users.find_one({"_id": ObjectId(acct)}, projection={"_id": 1}):
        return ObjectId(acct)
    if data.get("customer_id"):
        bc = await db.billing_customers.find_one({"paddle_customer_id": data["customer_id"]})
        if bc:
            return bc["account_id"]
    return None


async def apply_subscription(data: dict[str, Any], *, occurred_at: Any, event_id: str) -> str:
    db = get_db()
    sub_id = data.get("id")
    if not sub_id:
        return "ignored"
    existing = await db.subscriptions.find_one({"paddle_subscription_id": sub_id})
    last = existing.get("last_event_occurred_at") if existing else None
    if last and occurred_at and occurred_at < last:
        log.info("paddle_stale_event_ignored", extra={"subscription": sub_id})
        return "stale"
    s = get_settings()
    if s.paddle_api_key.get_secret_value() and s.paddle_enabled:
        try:
            data = {**data, **(await paddle.get_subscription(sub_id))}  # authoritative state
        except Exception as exc:  # noqa: BLE001
            log.warning("paddle_refetch_failed_using_payload", extra={"error": type(exc).__name__})
    account_id = existing["account_id"] if existing else await _account_for(data)
    if account_id is None:
        raise ValueError("Subscription cannot be matched to an account (missing custom_data.account_id).")
    items = data.get("items") or []
    price = (items[0].get("price") or {}) if items else {}
    price_id = price.get("id")
    mapping = await db.price_mappings.find_one({"paddle_price_id": price_id}) if price_id else None
    status = data.get("status") if data.get("status") in KNOWN_STATUSES else "canceled"
    period = data.get("current_billing_period") or {}
    now = utcnow()
    upd: dict[str, Any] = {
        "account_id": account_id,
        "source": "paddle",
        "status": status,
        "paddle_subscription_id": sub_id,
        "paddle_customer_id": data.get("customer_id"),
        "paddle_price_id": price_id,
        "paddle_product_id": price.get("product_id"),
        "billing_interval": (price.get("billing_cycle") or {}).get("interval") or (mapping or {}).get("interval"),
        "currency": data.get("currency_code"),
        "current_period_start": parse_iso(period.get("starts_at")),
        "current_period_end": parse_iso(period.get("ends_at")),
        "scheduled_change": data.get("scheduled_change"),
        "canceled_at": parse_iso(data.get("canceled_at")),
        "paused_at": parse_iso(data.get("paused_at")),
        "last_event_occurred_at": occurred_at or now,
        "last_event_id": event_id,
        "updated_at": now,
    }
    if mapping:
        if not existing or existing.get("plan_id") != mapping["plan_id"]:
            plan = await db.plans.find_one({"_id": mapping["plan_id"]})
            upd["plan_id"] = mapping["plan_id"]
            upd["plan_version"] = plan["current_version"] if plan else 1
        upd["unmapped_price"] = False
    else:
        # Unknown price: never grant entitlements we can't prove were purchased.
        upd["plan_id"] = None
        upd["unmapped_price"] = True
        log.error("paddle_unmapped_price", extra={"price_id": price_id})
    if status == "past_due" and not (existing or {}).get("past_due_since"):
        upd["past_due_since"] = now
    if status != "past_due":
        upd["past_due_since"] = None
    await db.subscriptions.update_one(
        {"paddle_subscription_id": sub_id},
        {
            "$set": upd,
            "$setOnInsert": {"created_at": now},
            "$push": {"history": {"$each": [{"event_id": event_id, "status": status, "price_id": price_id, "at": now}], "$slice": -50}},
        },
        upsert=True,
    )
    if data.get("customer_id"):
        try:
            await db.billing_customers.update_one(
                {"account_id": account_id}, {"$set": {"paddle_customer_id": data["customer_id"], "updated_at": now}}, upsert=True
            )
        except DuplicateKeyError:
            log.error("paddle_customer_conflict", extra={"account": str(account_id)})
    await audit.record(
        "billing.subscription_updated",
        actor_type="webhook",
        target_type="user",
        target_id=account_id,
        details={"status": status, "price_id": price_id, "mapped": bool(mapping), "event_id": event_id},
    )
    return "processed"
