from __future__ import annotations

from datetime import timedelta

from bson import ObjectId

from app.core.timeutil import utcnow
from app.database.client import mongo
from app.security.passwords import hash_password_sync, verify_password_sync
from tests.conftest import make_session


def test_argon2id_hash_and_verify():
    h = hash_password_sync("a-strong-password-123")
    assert h.startswith("$argon2id$")
    assert verify_password_sync(h, "a-strong-password-123")
    assert not verify_password_sync(h, "wrong")
    assert not verify_password_sync(None, "anything")


async def test_register_login_logout_flow(client):
    r = await client.post("/api/v1/auth/register", json={"email": "Alice@Example.com", "password": "correct-horse-battery"})
    assert r.status_code == 201, r.text
    assert "ks_session" in r.cookies and "ks_csrf" in r.cookies
    me = await client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["email"] == "Alice@Example.com"
    # Session tokens are stored hashed only.
    sess = await mongo.db.sessions.find_one({})
    assert sess["token_hash"] != client.cookies.get("ks_session")
    assert len(sess["token_hash"]) == 64
    # Logout requires CSRF header.
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = await client.post("/api/v1/auth/logout", headers={"x-csrf-token": client.cookies.get("ks_csrf")})
    assert r.status_code == 200
    client.cookies.clear()
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_login_generic_errors_and_no_enumeration(client):
    await client.post("/api/v1/auth/register", json={"email": "bob@example.com", "password": "correct-horse-battery"})
    client.cookies.clear()
    bad_pw = await client.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "nope-nope-nope"})
    no_user = await client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json()["error"]["message"] == no_user.json()["error"]["message"]
    ok = await client.post("/api/v1/auth/login", json={"email": "BOB@example.com", "password": "correct-horse-battery"})
    assert ok.status_code == 200


async def test_password_reset_is_uniform(client):
    a = await client.post("/api/v1/auth/password/forgot", json={"email": "ghost@example.com"})
    await client.post("/api/v1/auth/register", json={"email": "real@example.com", "password": "correct-horse-battery"})
    b = await client.post("/api/v1/auth/password/forgot", json={"email": "real@example.com"})
    assert a.status_code == b.status_code == 200
    assert a.json()["message"] == b.json()["message"]


async def test_password_reset_token_single_use_and_revokes_sessions(app, client):
    from app.auth import service

    s = await make_session(app, email="reset@example.com")
    user = await mongo.db.users.find_one({"email_normalized": "reset@example.com"})
    # Issue a token directly (email delivery disabled in tests).
    from app.security.tokens import new_token, token_hash

    tok = new_token(32)
    await mongo.db.password_reset_tokens.insert_one(
        {
            "user_id": user["_id"],
            "token_hash": token_hash(tok, purpose="reset"),
            "expires_at": utcnow() + timedelta(minutes=30),
            "used_at": None,
            "created_at": utcnow(),
        }
    )
    r = await client.post("/api/v1/auth/password/reset", json={"token": tok, "password": "brand-new-password-1"})
    assert r.status_code == 200
    r2 = await client.post("/api/v1/auth/password/reset", json={"token": tok, "password": "another-password-22"})
    assert r2.status_code == 422
    assert (await s.get("/api/v1/me")).status_code == 401  # old session revoked
    assert await service.authenticate("reset@example.com", "brand-new-password-1")


async def test_session_expiry_and_idle(app):
    s = await make_session(app)
    await mongo.db.sessions.update_many({}, {"$set": {"last_seen_at": utcnow() - timedelta(days=30)}})
    assert (await s.get("/api/v1/me")).status_code == 401
    s2 = await make_session(app)
    await mongo.db.sessions.update_many({"user_id": ObjectId(s2.user["id"])}, {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}})
    assert (await s2.get("/api/v1/me")).status_code == 401


async def test_session_rotation(app):
    s = await make_session(app)
    old = s.c.cookies.get("ks_session")
    await mongo.db.sessions.update_many({"user_id": ObjectId(s.user["id"])}, {"$set": {"rotated_at": utcnow() - timedelta(days=2)}})
    r = await s.get("/api/v1/auth/session")
    assert r.json()["authenticated"]
    new = s.c.cookies.get("ks_session")
    assert new and new != old
    assert (await s.get("/api/v1/me")).status_code == 200


async def test_cross_origin_post_rejected(client):
    r = await client.post(
        "/api/v1/auth/login", json={"email": "x@example.com", "password": "y"}, headers={"origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin_rejected"


async def test_login_rate_limited(client):
    for _ in range(10):
        await client.post("/api/v1/auth/login", json={"email": "rl@example.com", "password": "wrong-password-xx"})
    r = await client.post("/api/v1/auth/login", json={"email": "rl@example.com", "password": "wrong-password-xx"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_registration_rejects_weak_password(client):
    r = await client.post("/api/v1/auth/register", json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 422


async def test_google_disabled_hides_endpoints(client):
    cfg = (await client.get("/api/v1/auth/config")).json()
    assert cfg["google_enabled"] is False
    r = await client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert r.status_code == 404


async def test_google_claim_validation():
    import time

    import pytest

    from app.auth.google import validate_claims
    from app.core.config import get_settings
    from app.core.errors import ValidationFailed

    cid = get_settings().google_client_id
    good = {"iss": "https://accounts.google.com", "aud": cid, "exp": time.time() + 60, "iat": time.time(), "nonce": "n1", "sub": "123"}
    validate_claims(good, "n1")
    for bad in ({"iss": "https://evil"}, {"aud": "other"}, {"exp": time.time() - 3600}, {"nonce": "n2"}):
        with pytest.raises(ValidationFailed):
            validate_claims({**good, **bad}, "n1")


async def test_user_cannot_self_promote(user):
    r = await user.patch("/api/v1/me", json={"role": "SUPER_ADMIN"})
    assert r.status_code == 422
    me = (await user.get("/api/v1/me")).json()
    assert me["role"] == "USER"


async def test_health_endpoints(client):
    assert (await client.get("/health/live")).json() == {"status": "alive"}
    r = await client.get("/health/ready")
    assert r.status_code == 200 and r.json()["ready"] is True
