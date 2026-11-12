from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from bson import ObjectId

from app.billing import paddle
from app.billing.entitlements import resolve
from app.core.timeutil import utcnow
from app.database.client import mongo

SECRET = "pdl_ntfset_test_secret"


async def map_premium(interval: str = "month", price_id: str = "pri_01premiummonthly0001") -> dict[str, Any]:
    plan = await mongo.db.plans.find_one({"slug": "premium"})
    await mongo.db.price_mappings.update_one(
        {"plan_id": plan["_id"], "interval": interval},
        {
            "$set": {
                "paddle_price_id": price_id,
                "active": True,
                "verified": True,
                "environment": "sandbox",
                "verified_price": {"unit_amount_minor": 799 if interval == "month" else 7900, "currency": "USD"},
            }
        },
        upsert=True,
    )
    return plan


def sub_event(
    account_id: str,
    *,
    status: str = "active",
    sub_id: str = "sub_01test",
    price_id: str = "pri_01premiummonthly0001",
    event_type: str = "subscription.activated",
    occurred_at: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    return {
        "event_id": event_id or f"evt_{uuid.uuid4().hex[:20]}",
        "event_type": event_type,
        "occurred_at": occurred_at or now.isoformat().replace("+00:00", "Z"),
        "notification_id": f"ntf_{uuid.uuid4().hex[:10]}",
        "data": {
            "id": sub_id,
            "status": status,
            "customer_id": "ctm_01test",
            "currency_code": "USD",
            "custom_data": {"account_id": account_id},
            "items": [
                {
                    "price": {"id": price_id, "product_id": "pro_01test", "billing_cycle": {"interval": "month", "frequency": 1}},
                    "quantity": 1,
                }
            ],
            "current_billing_period": {
                "starts_at": (now - timedelta(days=1)).isoformat(),
                "ends_at": (now + timedelta(days=29)).isoformat(),
            },
            "scheduled_change": None,
            "address": {"postal_code": "SECRET"},
        },
    }


async def deliver(client, payload: dict[str, Any], *, secret: str = SECRET, ts: int | None = None, tamper: bool = False):
    raw = json.dumps(payload).encode()
    sig = paddle.sign_for_tests(raw, secret, ts)
    if tamper:
        raw = raw.replace(b'"active"', b'"trialing"')
    return await client.post(
        "/api/v1/billing/webhooks/paddle", content=raw, headers={"paddle-signature": sig, "content-type": "application/json"}
    )


def test_signature_verification_unit():
    body = b'{"a":1}'
    now = time.time()
    good = paddle.sign_for_tests(body, "s3cret", int(now))
    assert paddle.verify_signature(body, good, "s3cret", tolerance_seconds=5, now=now) == int(now)
    with pytest.raises(paddle.SignatureError):
        paddle.verify_signature(body + b" ", good, "s3cret", tolerance_seconds=5, now=now)  # body altered
    with pytest.raises(paddle.SignatureError):
        paddle.verify_signature(body, good, "other", tolerance_seconds=5, now=now)
    with pytest.raises(paddle.SignatureError):
        paddle.verify_signature(body, good, "s3cret", tolerance_seconds=5, now=now + 60)  # replayed later
    with pytest.raises(paddle.SignatureError):
        paddle.verify_signature(body, None, "s3cret", tolerance_seconds=5)
    rotated = f"ts={int(now)};h1=deadbeef;" + good.split(";")[1]
    assert paddle.verify_signature(body, rotated, "s3cret", tolerance_seconds=5, now=now)


async def test_forged_webhook_rejected_and_grants_nothing(client, user):
    await map_premium()
    r = await deliver(client, sub_event(user.user["id"]), secret="wrong-secret")
    assert r.status_code == 401
    r2 = await deliver(client, sub_event(user.user["id"]), tamper=True)
    assert r2.status_code == 401
    r3 = await deliver(client, sub_event(user.user["id"]), ts=int(time.time()) - 3600)
    assert r3.status_code == 401
    assert (await resolve(ObjectId(user.user["id"]))).plan_slug == "free"
    assert await mongo.db.billing_events.count_documents({}) == 0
    assert await mongo.db.webhook_rejections.count_documents({}) == 3


async def test_verified_webhook_grants_premium_and_strips_pii(client, user):
    await map_premium()
    r = await deliver(client, sub_event(user.user["id"]))
    assert r.status_code == 200 and r.json()["status"] == "processed"
    res = await resolve(ObjectId(user.user["id"]))
    assert res.plan_slug == "premium" and res.source == "paddle"
    sub = await mongo.db.subscriptions.find_one({"paddle_subscription_id": "sub_01test"})
    assert sub["paddle_customer_id"] == "ctm_01test" and sub["paddle_price_id"] and sub["plan_version"] == 1
    assert sub["current_period_start"] and sub["billing_interval"] == "month"
    ev = await mongo.db.billing_events.find_one({})
    assert "address" not in ev["payload"]["data"]


async def test_duplicate_webhooks_do_not_duplicate(client, user):
    await map_premium()
    payload = sub_event(user.user["id"], event_id="evt_dup_1")
    a = await deliver(client, payload)
    b = await deliver(client, payload)
    assert a.json()["status"] == "processed" and b.json()["status"] == "duplicate"
    assert await mongo.db.subscriptions.count_documents({"source": "paddle"}) == 1
    assert await mongo.db.billing_events.count_documents({}) == 1
    sub = await mongo.db.subscriptions.find_one({"source": "paddle"})
    assert len(sub["history"]) == 1


async def test_out_of_order_events(client, user):
    await map_premium()
    now = utcnow()
    newer = sub_event(user.user["id"], status="canceled", event_type="subscription.canceled", occurred_at=now.isoformat())
    older = sub_event(user.user["id"], status="active", occurred_at=(now - timedelta(minutes=5)).isoformat())
    assert (await deliver(client, newer)).json()["status"] == "processed"
    assert (await deliver(client, older)).json()["status"] == "stale"
    assert (await mongo.db.subscriptions.find_one({"source": "paddle"}))["status"] == "canceled"
    assert (await resolve(ObjectId(user.user["id"]))).plan_slug == "free"


async def test_failed_payment_and_grace_period(client, user):
    await map_premium()
    acct = ObjectId(user.user["id"])
    await deliver(client, sub_event(user.user["id"], occurred_at=(utcnow() - timedelta(minutes=2)).isoformat()))
    assert (await resolve(acct)).plan_slug == "premium"
    await deliver(client, sub_event(user.user["id"], status="past_due", event_type="subscription.past_due"))
    r = await resolve(acct)
    assert r.plan_slug == "premium" and "payment_past_due" in r.notices  # within the configured grace period
    await mongo.db.subscriptions.update_one({"source": "paddle"}, {"$set": {"past_due_since": utcnow() - timedelta(days=10)}})
    assert (await resolve(acct)).plan_slug == "free"


async def test_new_past_due_subscription_never_granted(client, user):
    await map_premium()
    await deliver(client, sub_event(user.user["id"], status="past_due", event_type="subscription.created"))
    await mongo.db.subscriptions.update_one({"source": "paddle"}, {"$set": {"past_due_since": utcnow() - timedelta(days=30)}})
    assert (await resolve(ObjectId(user.user["id"]))).plan_slug == "free"


async def test_unmapped_price_grants_nothing(client, user):
    r = await deliver(client, sub_event(user.user["id"], price_id="pri_01unknownprice0000"))
    assert r.status_code == 200
    sub = await mongo.db.subscriptions.find_one({"source": "paddle"})
    assert sub["unmapped_price"] is True and sub["plan_id"] is None
    assert (await resolve(ObjectId(user.user["id"]))).plan_slug == "free"


async def test_unknown_account_is_retried_not_granted(client):
    await map_premium()
    r = await deliver(client, sub_event(str(ObjectId())))
    assert r.status_code == 500  # Paddle retries; nothing granted
    ev = await mongo.db.billing_events.find_one({})
    assert ev["processing_status"] == "failed"


async def test_checkout_redirect_does_not_grant_access(user):
    # Visiting the success URL or reading status never changes entitlements.
    r = await user.get("/api/v1/billing/subscription", params={"checkout": "complete"})
    assert r.status_code == 200 and r.json()["entitlements"]["plan"]["slug"] == "free"


async def test_checkout_without_api_key_fails_clearly(user):
    plan = await map_premium()
    r = await user.post("/api/v1/billing/checkout", json={"plan_id": str(plan["_id"]), "interval": "month"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "billing_not_configured"


async def test_existing_subscriptions_not_repriced_by_mapping_change(client, super_admin, user):
    await map_premium()
    await deliver(client, sub_event(user.user["id"]))
    plan = await mongo.db.plans.find_one({"slug": "premium"})
    r = await super_admin.put(
        "/api/v1/admin/price-mappings",
        json={
            "plan_id": str(plan["_id"]),
            "interval": "month",
            "paddle_price_id": "pri_01newprice000000000",
            "reason": "new price for new customers",
        },
    )
    assert r.status_code == 200, r.text
    sub = await mongo.db.subscriptions.find_one({"source": "paddle"})
    assert sub["paddle_price_id"] == "pri_01premiummonthly0001"  # untouched
    audit = await mongo.db.audit_events.find_one({"action": "billing.price_mapping_changed"})
    assert audit["details"]["from"] == "pri_01premiummonthly0001" and audit["details"]["to"] == "pri_01newprice000000000"
    assert audit["actor_id"] == ObjectId(super_admin.user["id"])


async def test_downgrade_via_cancellation_keeps_files(client, user):
    await map_premium()
    await deliver(client, sub_event(user.user["id"], occurred_at=(utcnow() - timedelta(minutes=1)).isoformat()))
    await deliver(client, sub_event(user.user["id"], status="canceled", event_type="subscription.canceled"))
    acct = ObjectId(user.user["id"])
    assert (await resolve(acct)).plan_slug == "free"
    assert (await user.get("/api/v1/files")).status_code == 200


async def test_env_price_ids_bootstrap_premium_mappings_once(app: Any, monkeypatch: Any) -> None:
    from app.billing.catalog import bootstrap_price_mappings
    from app.core.config import get_settings
    from app.database.client import mongo

    s = get_settings()
    monkeypatch.setattr(s, "paddle_price_id_premium_monthly", "pri_01hmonthlyabcdef")
    monkeypatch.setattr(s, "paddle_price_id_premium_yearly", "not-a-price")
    out = await bootstrap_price_mappings()
    assert out == {"month": "mapped (unverified)", "year": "invalid price id format (expected pri_...)"}
    m = await mongo.db.price_mappings.find_one({"paddle_price_id": "pri_01hmonthlyabcdef"})
    assert m and m["verified"] is False and m["interval"] == "month" and m["active"] is True
    # An admin later maps a different price: the environment never overwrites it.
    await mongo.db.price_mappings.update_one({"_id": m["_id"]}, {"$set": {"paddle_price_id": "pri_01hadminchosen99"}})
    assert (await bootstrap_price_mappings())["month"] == "kept existing mapping"
    assert await mongo.db.price_mappings.count_documents({}) == 1
    assert await mongo.db.audit_events.count_documents({"action": "billing.price_mapping_changed", "actor_type": "system"}) == 1
