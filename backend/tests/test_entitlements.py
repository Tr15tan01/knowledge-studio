from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.billing import catalog, usage
from app.billing.entitlements import reserve_metric, resolve
from app.billing.periods import calendar_month, subscription_month
from app.billing.schema import PlanUpdate, default_free_entitlements
from app.core.errors import QuotaExceeded, ValidationFailed
from app.core.timeutil import utcnow
from app.core.units import GIB, MIB
from app.database.client import mongo


async def _premium_sub(account_id, *, start=None, end=None, status="active", interval="month"):
    plan = await catalog.get_plan_by_slug("premium")
    start = start or utcnow() - timedelta(days=3)
    end = end or start + (timedelta(days=30) if interval == "month" else timedelta(days=365))
    await mongo.db.subscriptions.insert_one(
        {
            "account_id": account_id,
            "source": "paddle",
            "plan_id": plan["_id"],
            "plan_version": plan["current_version"],
            "status": status,
            "paddle_subscription_id": f"sub_{ObjectId()}",
            "current_period_start": start,
            "current_period_end": end,
            "billing_interval": interval,
            "created_at": utcnow(),
        }
    )


async def test_free_defaults_match_seed():
    acct = ObjectId()
    r = await resolve(acct)
    assert r.plan_slug == "free" and r.source == "default"
    e = r.entitlements
    assert e.upload_limit_per_period == 5
    assert e.storage_limit_bytes == 500 * MIB
    assert e.max_file_size_bytes == 25 * MIB
    assert e.question_limit_per_period == 30
    assert e.research_report_limit_per_period == 1
    assert e.max_concurrent_research_jobs == 1
    assert r.has_feature("markdown_export") and not r.has_feature("pdf_export")
    assert r.period.kind == "calendar_month"


async def test_premium_limits_and_subscription_period():
    acct = ObjectId()
    await _premium_sub(acct)
    r = await resolve(acct)
    e = r.entitlements
    assert r.plan_slug == "premium" and r.source == "paddle"
    assert (e.upload_limit_per_period, e.storage_limit_bytes, e.max_file_size_bytes) == (20, 5 * GIB, 100 * MIB)
    assert (e.question_limit_per_period, e.research_report_limit_per_period, e.max_concurrent_research_jobs) == (100, 10, 2)
    assert r.has_feature("pdf_export") and r.has_feature("cross_document_research")
    assert r.period.kind == "subscription"


async def test_failed_payment_does_not_grant_premium():
    acct = ObjectId()
    await _premium_sub(acct, status="past_due")
    await mongo.db.subscriptions.update_one({"account_id": acct}, {"$set": {"past_due_since": utcnow() - timedelta(days=10)}})
    assert (await resolve(acct)).plan_slug == "free"
    acct2 = ObjectId()
    await _premium_sub(acct2, status="canceled")
    assert (await resolve(acct2)).plan_slug == "free"
    acct3 = ObjectId()
    await _premium_sub(acct3, status="paused")
    assert (await resolve(acct3)).plan_slug == "free"


def test_calendar_month_boundaries():
    p = calendar_month(datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC))
    assert p.start == datetime(2026, 2, 1, tzinfo=UTC) and p.end == datetime(2026, 3, 1, tzinfo=UTC)
    p2 = calendar_month(datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC))
    assert p2.key == "m:2026-03" and p.key != p2.key
    p3 = calendar_month(datetime(2026, 12, 31, 12, tzinfo=UTC))
    assert p3.end == datetime(2027, 1, 1, tzinfo=UTC)


def test_subscription_month_anchor_clamps_month_end():
    anchor = datetime(2026, 1, 31, 10, tzinfo=UTC)
    annual_end = datetime(2027, 1, 31, 10, tzinfo=UTC)
    feb = subscription_month(anchor, datetime(2026, 2, 15, tzinfo=UTC), annual_end, "s1")
    assert feb.start == anchor and feb.end == datetime(2026, 2, 28, 10, tzinfo=UTC)
    mar = subscription_month(anchor, datetime(2026, 3, 1, tzinfo=UTC), annual_end, "s1")
    assert mar.start == datetime(2026, 2, 28, 10, tzinfo=UTC) and mar.end == datetime(2026, 3, 31, 10, tzinfo=UTC)
    # Exactly at boundary: belongs to the next window.
    edge = subscription_month(anchor, datetime(2026, 3, 31, 10, tzinfo=UTC), annual_end, "s1")
    assert edge.start == datetime(2026, 3, 31, 10, tzinfo=UTC)
    # Monthly subscription window ends at the Paddle period end.
    m = subscription_month(anchor, datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 2, 28, 10, tzinfo=UTC), "s2")
    assert m.end == datetime(2026, 2, 28, 10, tzinfo=UTC)
    # Stale period data falls back to calendar month rather than guessing.
    stale = subscription_month(anchor, datetime(2027, 6, 1, tzinfo=UTC), annual_end, "s1")
    assert stale.kind == "calendar_month"


async def test_concurrent_question_reservations_never_exceed_limit():
    acct = ObjectId()
    r = await resolve(acct)
    limit = r.limit("questions")
    assert limit == 30

    async def attempt(i: int):
        try:
            return await reserve_metric(r, "questions", 1, idempotency_key=f"q-{acct}-{i}")
        except QuotaExceeded:
            return None

    results = await asyncio.gather(*(attempt(i) for i in range(80)))
    granted = [x for x in results if x]
    assert len(granted) == limit
    snap = await usage.usage_snapshot(acct, "questions", r.period)
    assert snap["reserved"] == limit
    # Commit half, release half; both are idempotent.
    for i, res in enumerate(granted):
        if i % 2:
            assert await usage.commit(res.id)
            assert not await usage.commit(res.id)
        else:
            assert await usage.release(res.id)
            assert not await usage.release(res.id)
    snap = await usage.usage_snapshot(acct, "questions", r.period)
    assert snap == {"used": 15, "reserved": 0}
    assert await mongo.db.usage_events.count_documents({"account_id": acct, "metric": "questions"}) == 15
    assert await mongo.db.usage_events.count_documents({"account_id": acct, "metric": "quota_denial"}) == 80 - limit


async def test_idempotent_replay_returns_same_reservation():
    acct = ObjectId()
    r = await resolve(acct)
    a = await reserve_metric(r, "questions", 1, idempotency_key="same-key")
    b = await reserve_metric(r, "questions", 1, idempotency_key="same-key")
    assert a.id == b.id and b.replayed
    assert (await usage.usage_snapshot(acct, "questions", r.period))["reserved"] == 1


async def test_expired_reservations_released_and_counter_reconciled():
    acct = ObjectId()
    r = await resolve(acct)
    res = await reserve_metric(r, "questions", 1, ttl_seconds=60)
    await mongo.db.usage_reservations.update_one({"_id": res.id}, {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}})
    # Simulate a crash that left a reservation in 'applying' with the counter incremented.
    cid = usage.counter_id(acct, "questions", r.period.key)
    await mongo.db.usage_reservations.insert_one(
        {
            "account_id": acct,
            "metric": "questions",
            "amount": 3,
            "period_key": r.period.key,
            "counter_id": cid,
            "state": "applying",
            "idempotency_key": "crashed",
            "meta": {},
            "created_at": utcnow() - timedelta(minutes=5),
            "updated_at": utcnow(),
            "expires_at": utcnow(),
        }
    )
    await mongo.db.usage_counters.update_one({"_id": cid}, {"$inc": {"reserved": 3, "version": 1}})
    n = await usage.expire_stale_reservations()
    assert n == 2
    assert await usage.usage_snapshot(acct, "questions", r.period) == {"used": 0, "reserved": 0}


async def test_reconcile_repairs_corrupted_counter():
    acct = ObjectId()
    r = await resolve(acct)
    res = await reserve_metric(r, "questions", 2)
    await usage.commit(res.id)
    cid = usage.counter_id(acct, "questions", r.period.key)
    await mongo.db.usage_counters.update_one({"_id": cid}, {"$set": {"used": 999}})
    await usage.reconcile_counter(cid)
    assert (await usage.usage_snapshot(acct, "questions", r.period))["used"] == 2


async def test_plan_version_new_subscribers_only_keeps_existing_entitlements(super_admin):
    existing = ObjectId()
    assert (await resolve(existing)).limit("questions") == 30  # creates pinned default assignment v1
    plan = await catalog.get_plan_by_slug("free")
    ents = default_free_entitlements().model_dump()
    ents["question_limit_per_period"] = 50
    with pytest.raises(ValidationFailed):  # significant change requires confirmation
        await catalog.update_plan(
            str(plan["_id"]), PlanUpdate(entitlements=ents, change_note="More questions"), ObjectId(super_admin.user["id"])
        )
    await catalog.update_plan(
        str(plan["_id"]), PlanUpdate(entitlements=ents, change_note="More questions", confirm=True), ObjectId(super_admin.user["id"])
    )
    assert (await resolve(existing)).limit("questions") == 30
    newcomer = ObjectId()
    assert (await resolve(newcomer)).limit("questions") == 50
    # Explicit later migration moves the existing subscriber.
    n = await catalog.migrate_subscribers(
        str(plan["_id"]), from_version=1, to_version=2, account_ids=None, actor_id=ObjectId(super_admin.user["id"]), reason="rollout"
    )
    assert n >= 1
    assert (await resolve(existing)).limit("questions") == 50
    hist = await catalog.plan_history(str(plan["_id"]))
    assert [h["version"] for h in hist] == [2, 1]
    assert await mongo.db.audit_events.count_documents({"action": {"$in": ["plan.version_published", "plan.subscribers_migrated"]}}) == 2


async def test_plan_version_all_and_cohort(super_admin):
    a, b, c = ObjectId(), ObjectId(), ObjectId()
    for x in (a, b, c):
        await resolve(x)
    plan = await catalog.get_plan_by_slug("free")
    actor = ObjectId(super_admin.user["id"])
    ents = default_free_entitlements().model_dump()
    ents["upload_limit_per_period"] = 7
    await catalog.update_plan(
        str(plan["_id"]),
        PlanUpdate(entitlements=ents, change_note="cohort test", apply_policy="cohort", cohort_account_ids=[str(a)], confirm=True),
        actor,
    )
    assert (await resolve(a)).limit("uploads") == 7
    assert (await resolve(b)).limit("uploads") == 5
    ents["upload_limit_per_period"] = 9
    await catalog.update_plan(
        str(plan["_id"]), PlanUpdate(entitlements=ents, change_note="all", apply_policy="all_subscribers", confirm=True), actor
    )
    assert {(await resolve(x)).limit("uploads") for x in (a, b, c)} == {9}


async def test_promotional_grant_and_expiration():
    acct = ObjectId()
    now = utcnow()
    await mongo.db.entitlement_grants.insert_one(
        {
            "account_id": acct,
            "kind": "promo",
            "overrides": {"question_limit_per_period": 200, "features": {"pdf_export": True}},
            "starts_at": now - timedelta(minutes=1),
            "expires_at": now + timedelta(days=7),
            "revoked_at": None,
            "reason": "launch promo",
        }
    )
    r = await resolve(acct)
    assert r.limit("questions") == 200 and r.has_feature("pdf_export")
    later = await resolve(acct, now=now + timedelta(days=8))
    assert later.limit("questions") == 30 and not later.has_feature("pdf_export")


async def test_quota_adjustment_applies_to_current_period_only():
    acct = ObjectId()
    r = await resolve(acct)
    now = utcnow()
    await mongo.db.entitlement_grants.insert_one(
        {
            "account_id": acct,
            "kind": "adjustment",
            "metric": "uploads",
            "delta": 3,
            "period_key": r.period.key,
            "starts_at": now - timedelta(minutes=1),
            "expires_at": r.period.end,
            "revoked_at": None,
            "reason": "support",
        }
    )
    assert (await resolve(acct)).limit("uploads") == 8


def test_entitlements_schema_rejects_unknown_fields_and_inconsistency():
    from pydantic import ValidationError

    from app.billing.schema import Entitlements

    base = default_free_entitlements().model_dump()
    with pytest.raises(ValidationError):
        Entitlements.model_validate({**base, "exec": "rm -rf /"})
    with pytest.raises(ValidationError):
        Entitlements.model_validate({**base, "features": {"teleport": True}})
    with pytest.raises(ValidationError):
        Entitlements.model_validate({**base, "max_file_size_bytes": base["storage_limit_bytes"] + 1})
    with pytest.raises(ValidationError):
        Entitlements.model_validate({**base, "question_limit_per_period": -1})


def test_binary_unit_display():
    from app.core.units import format_bytes

    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1 KiB"
    assert format_bytes(500 * MIB) == "500 MiB"
    assert format_bytes(124 * MIB + MIB // 2) == "124.5 MiB"
    assert format_bytes(5 * GIB) == "5 GiB"
    assert MIB == 1_048_576 and GIB == 1_073_741_824
