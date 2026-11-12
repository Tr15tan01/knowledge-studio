from __future__ import annotations

import json
from datetime import timedelta

from bson import ObjectId

from app.billing.entitlements import resolve
from app.billing.schema import default_free_entitlements
from app.core.timeutil import utcnow
from app.database.client import mongo
from tests.conftest import make_session

ADMIN_GETS = [
    "/api/v1/admin/overview",
    "/api/v1/admin/plans",
    "/api/v1/admin/users",
    "/api/v1/admin/usage",
    "/api/v1/admin/jobs",
    "/api/v1/admin/health",
    "/api/v1/admin/config",
    "/api/v1/admin/audit",
    "/api/v1/admin/price-mappings",
    "/api/v1/admin/billing/events",
]


async def test_unauthenticated_and_ordinary_users_denied(client, user):
    for path in ADMIN_GETS:
        assert (await client.get(path)).status_code == 401, path
        assert (await user.get(path)).status_code == 403, path
    r = await user.post("/api/v1/admin/plans", json={})
    assert r.status_code in (403, 422)
    r = await user.post(f"/api/v1/admin/users/{user.user['id']}/role", json={"role": "SUPER_ADMIN", "reason": "please"})
    assert r.status_code == 403
    assert (await mongo.db.users.find_one({"_id": ObjectId(user.user["id"])}))["role"] == "USER"


async def test_admin_vs_super_admin_permissions(admin, super_admin):
    for path in ADMIN_GETS:
        assert (await admin.get(path)).status_code == 200, path
    plan = await mongo.db.plans.find_one({"slug": "free"})
    ents = default_free_entitlements().model_dump()
    body = {"entitlements": ents, "change_note": "try", "confirm": True}
    assert (await admin.patch(f"/api/v1/admin/plans/{plan['_id']}", json=body)).status_code == 403
    assert (await super_admin.patch(f"/api/v1/admin/plans/{plan['_id']}", json=body)).status_code == 200
    assert (
        await admin.put(
            "/api/v1/admin/price-mappings",
            json={"plan_id": str(plan["_id"]), "interval": "month", "paddle_price_id": "pri_01aaaaaaaaaaaaaa", "reason": "x x"},
        )
    ).status_code == 403
    assert (
        await admin.post(f"/api/v1/admin/users/{admin.user['id']}/role", json={"role": "SUPER_ADMIN", "reason": "self promote"})
    ).status_code == 403


async def test_plan_edits_validated_server_side(super_admin):
    plan = await mongo.db.plans.find_one({"slug": "free"})
    bad = default_free_entitlements().model_dump()
    bad["question_limit_per_period"] = -5
    r = await super_admin.patch(f"/api/v1/admin/plans/{plan['_id']}", json={"entitlements": bad, "change_note": "bad", "confirm": True})
    assert r.status_code == 422
    unknown = default_free_entitlements().model_dump() | {"run_script": "x"}
    assert (
        await super_admin.patch(f"/api/v1/admin/plans/{plan['_id']}", json={"entitlements": unknown, "change_note": "bad", "confirm": True})
    ).status_code == 422
    # Significant change without confirmation returns the impact instead of publishing.
    ok = default_free_entitlements().model_dump() | {"upload_limit_per_period": 9}
    r = await super_admin.patch(f"/api/v1/admin/plans/{plan['_id']}", json={"entitlements": ok, "change_note": "more uploads"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "confirmation_required"
    r = await super_admin.patch(
        f"/api/v1/admin/plans/{plan['_id']}",
        json={"entitlements": ok, "change_note": "more uploads", "confirm": True, "apply_policy": "all_subscribers"},
    )
    assert r.status_code == 200 and r.json()["current_version"] == 2
    hist = (await super_admin.get(f"/api/v1/admin/plans/{plan['_id']}/history")).json()["items"]
    assert [h["version"] for h in hist] == [2, 1] and hist[0]["change_note"] == "more uploads"


async def test_admin_can_change_limits_prices_and_features_without_code(super_admin, user):
    plan = await mongo.db.plans.find_one({"slug": "free"})
    ents = default_free_entitlements().model_dump()
    ents.update(
        {
            "upload_limit_per_period": 12,
            "storage_limit_bytes": 1024**3,
            "max_file_size_bytes": 50 * 1024**2,
            "question_limit_per_period": 77,
            "research_report_limit_per_period": 3,
        }
    )
    ents["features"]["pdf_export"] = True
    r = await super_admin.patch(
        f"/api/v1/admin/plans/{plan['_id']}",
        json={
            "entitlements": ents,
            "pricing": {"currency": "USD", "monthly_display_price_minor": 0, "annual_display_price_minor": 0},
            "name": "Starter",
            "change_note": "launch tweak",
            "confirm": True,
            "apply_policy": "all_subscribers",
        },
    )
    assert r.status_code == 200
    u = (await user.get("/api/v1/me/usage")).json()
    assert u["entitlements"]["limits"]["upload_limit_per_period"] == 12 and u["entitlements"]["limits"]["question_limit_per_period"] == 77
    assert u["storage"]["display"]["limit"] == "1 GiB" and u["storage"]["display"]["max_file_size"] == "50 MiB"
    assert any(f["key"] == "pdf_export" and f["enabled"] for f in u["entitlements"]["features"])
    pricing = (await user.get("/api/v1/plans")).json()
    assert pricing["plans"][0]["name"] == "Starter"


async def test_create_and_archive_plan_safely(super_admin, user):
    body = {
        "name": "Student",
        "slug": "student",
        "description": "For students",
        "is_public": False,
        "display_order": 15,
        "pricing": {"currency": "USD", "monthly_display_price_minor": 399},
        "entitlements": default_free_entitlements().model_dump(),
    }
    r = await super_admin.post("/api/v1/admin/plans", json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert (await super_admin.post("/api/v1/admin/plans", json=body)).status_code == 409  # duplicate slug
    # Give a user an active assignment on this plan, then try to archive without policy.
    await mongo.db.subscriptions.insert_one(
        {"account_id": ObjectId(user.user["id"]), "source": "manual", "plan_id": ObjectId(pid), "plan_version": 1, "status": "active"}
    )
    r = await super_admin.post(f"/api/v1/admin/plans/{pid}/archive", json={"reason": "retire"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "archive_requires_policy"
    r = await super_admin.post(f"/api/v1/admin/plans/{pid}/archive", json={"reason": "retire", "grandfather": True})
    assert r.status_code == 200 and r.json()["archived_at"]
    free = await mongo.db.plans.find_one({"slug": "free"})
    r = await super_admin.post(f"/api/v1/admin/plans/{free['_id']}/archive", json={"reason": "no", "grandfather": True})
    assert r.status_code == 422  # default plan can't be archived


async def test_secret_values_never_returned(super_admin):
    from app.core.config import get_settings

    secret = get_settings().paddle_webhook_secret.get_secret_value()
    session_secret = get_settings().session_secret.get_secret_value()
    for path in (
        "/api/v1/admin/config",
        "/api/v1/admin/health",
        "/api/v1/admin/users",
        f"/api/v1/admin/users/{super_admin.user['id']}",
        "/api/v1/admin/audit",
    ):
        text = (await super_admin.get(path)).text
        assert secret not in text and session_secret not in text and "password_hash" not in text and "token_hash" not in text, path
        assert "argon2" not in text
    cfg = (await super_admin.get("/api/v1/admin/config")).json()
    assert cfg["integrations"]["paddle"]["webhook_secret"] == "configured"


async def test_last_super_admin_cannot_remove_own_access(super_admin):
    sid = super_admin.user["id"]
    r = await super_admin.post(f"/api/v1/admin/users/{sid}/role", json={"role": "ADMIN", "reason": "step down"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "last_super_admin"
    r = await super_admin.post(f"/api/v1/admin/users/{sid}/suspend", json={"reason": "oops"})
    assert r.status_code == 422
    r = await super_admin.delete("/api/v1/me", json={"confirm_email": (await mongo.db.users.find_one({"_id": ObjectId(sid)}))["email"]})
    assert r.status_code == 422


async def test_sensitive_actions_require_recent_auth(super_admin):
    await mongo.db.sessions.update_many(
        {"user_id": ObjectId(super_admin.user["id"])}, {"$set": {"reauth_at": utcnow() - timedelta(hours=2)}}
    )
    plan = await mongo.db.plans.find_one({"slug": "free"})
    r = await super_admin.patch(f"/api/v1/admin/plans/{plan['_id']}", json={"description": "new", "change_note": "copy"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauth_required"
    assert (await super_admin.post("/api/v1/auth/reauthenticate", json={"password": "correct-horse-battery"})).status_code == 200
    r = await super_admin.patch(f"/api/v1/admin/plans/{plan['_id']}", json={"description": "new", "change_note": "copy"})
    assert r.status_code == 200


async def test_every_sensitive_action_is_audited(app, super_admin, user):
    uid = user.user["id"]
    plan = await mongo.db.plans.find_one({"slug": "premium"})
    actions = [
        ("post", f"/api/v1/admin/users/{uid}/suspend", {"reason": "abuse report"}, "admin.user_suspended"),
        ("post", f"/api/v1/admin/users/{uid}/restore", {"reason": "resolved"}, "admin.user_restored"),
        (
            "post",
            f"/api/v1/admin/users/{uid}/quota-adjustments",
            {"metric": "questions", "delta": 10, "reason": "support goodwill"},
            "admin.quota_adjusted",
        ),
        (
            "post",
            f"/api/v1/admin/users/{uid}/grants",
            {"plan_id": str(plan["_id"]), "expires_at": (utcnow() + timedelta(days=7)).isoformat(), "reason": "beta tester"},
            "admin.entitlement_granted",
        ),
        (
            "put",
            "/api/v1/admin/price-mappings",
            {"plan_id": str(plan["_id"]), "interval": "year", "paddle_price_id": "pri_01yearly000000000000", "reason": "annual price"},
            "billing.price_mapping_changed",
        ),
        (
            "put",
            "/api/v1/admin/quota-policy",
            {"upload_quota_restore_on_delete": True, "reason": "be generous"},
            "admin.quota_policy_changed",
        ),
        ("post", f"/api/v1/admin/users/{uid}/role", {"role": "ADMIN", "reason": "new teammate"}, "admin.role_changed"),
    ]
    for method, path, body, action in actions:
        r = await getattr(super_admin, method)(path, json=body)
        assert r.status_code in (200, 201), (path, r.text)
        ev = await mongo.db.audit_events.find_one({"action": action}, sort=[("created_at", -1)])
        assert ev and ev["actor_id"] == ObjectId(super_admin.user["id"]) and ev.get("reason"), action
    # Grants take effect and reasons are required.
    assert (await resolve(ObjectId(uid))).plan_slug == "free"  # plan is still the default assignment
    assert (await resolve(ObjectId(uid))).limit("questions") == 100 + 10  # promo grant (Premium) + adjustment
    r = await super_admin.post(f"/api/v1/admin/users/{uid}/suspend", json={})
    assert r.status_code == 422


async def test_suspension_revokes_sessions(admin, user):
    r = await admin.post(f"/api/v1/admin/users/{user.user['id']}/suspend", json={"reason": "spam uploads"})
    assert r.status_code == 200
    assert (await user.get("/api/v1/me")).status_code == 401


async def test_audit_log_is_not_editable(super_admin):
    from app.main import app as application

    paths = {getattr(r, "path", "") + ":" + ",".join(sorted(getattr(r, "methods", []) or [])) for r in application.routes}
    assert not any("audit" in p and ("DELETE" in p or "PATCH" in p or "PUT" in p) for p in paths)


async def test_admin_job_retry_is_idempotent_and_safe(admin, user):
    from app.jobs import queue

    jid = await queue.enqueue("ingest.file", {"file_id": str(ObjectId())}, dedupe_key="x1")
    await mongo.db.jobs.update_one({"_id": jid}, {"$set": {"status": "failed"}})
    assert (await admin.post(f"/api/v1/admin/jobs/{jid}/retry", json={"reason": "provider fixed"})).status_code == 200
    assert (await admin.post(f"/api/v1/admin/jobs/{jid}/retry", json={"reason": "again"})).status_code == 422
    rj = await queue.enqueue("research.run", {"research_job_id": str(ObjectId())})
    await mongo.db.jobs.update_one({"_id": rj}, {"$set": {"status": "failed"}})
    assert (await admin.post(f"/api/v1/admin/jobs/{rj}/retry", json={"reason": "no"})).status_code == 422
    assert await mongo.db.jobs.count_documents({}) == 2


async def test_usage_csv_minimizes_data(admin, user):
    from tests.test_storage_uploads import upload

    await upload(user, "a.txt", b"hello csv export")
    r = await admin.get("/api/v1/admin/usage/export.csv")
    assert r.status_code == 200 and r.text.startswith("account_id,")
    assert "@" not in r.text
    assert user.user["id"] in r.text


async def test_overview_metrics_are_real(admin, user):
    from tests.test_storage_uploads import upload

    await upload(user, "x.txt", b"metrics test data")
    o = (await admin.get("/api/v1/admin/overview", params={"days": 7})).json()
    assert o["users"]["total"] >= 2 and o["storage"]["files"] == 1 and o["usage"]["uploads"] == 1
    assert o["mrr"]["is_estimate"] and o["provider"]["cost_is_estimate"]
    assert len(o["timeseries"]) == 7


async def test_cli_create_admin_requires_existing_account(app, capsys):
    from app.cli import cmd_create_admin

    class A:
        email = "nobody@example.com"
        role = "SUPER_ADMIN"
        reason = ""
        allow_unverified = True
        yes = True

    from app.database import client as dbclient

    original = dbclient.Mongo.connect

    async def noop(self, s):  # reuse the test connection
        return None

    dbclient.Mongo.connect = noop
    try:
        assert await cmd_create_admin(A()) == 2
        s = await make_session(app, email="boss@example.com")
        A.email = "boss@example.com"
        A.allow_unverified = False
        assert await cmd_create_admin(A()) == 2  # unverified email refused
        A.allow_unverified = True
        assert await cmd_create_admin(A()) == 0
        u = await mongo.db.users.find_one({"email_normalized": "boss@example.com"})
        assert u["role"] == "SUPER_ADMIN"
        out = capsys.readouterr().out
        assert "password" not in out.lower() or "known credentials" in out.lower()
        ev = await mongo.db.audit_events.find_one({"action": "admin.role_granted"})
        assert ev["actor_type"] == "cli"
        await s.c.aclose()
    finally:
        dbclient.Mongo.connect = original


_ = json
