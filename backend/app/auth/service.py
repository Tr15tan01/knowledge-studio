"""Authentication, sessions, verification and password reset."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, Unauthorized, ValidationFailed
from app.core.timeutil import ensure_utc, require_utc, utcnow
from app.database.client import get_db
from app.security.passwords import hash_password, needs_rehash, password_problems, verify_password
from app.security.tokens import new_token, token_hash
from app.services import audit
from app.services.email import send_email

log = logging.getLogger("app.auth")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
ROLES = ("USER", "ADMIN", "SUPER_ADMIN")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str:
    e = email.strip()
    if not EMAIL_RE.match(e) or len(e) > 320:
        raise ValidationFailed("Please enter a valid email address.", details={"field": "email"})
    return e


@dataclass
class IssuedSession:
    token: str
    csrf_token: str
    session_id: ObjectId
    expires_at: Any


async def create_personal_workspace(user_id: ObjectId, name: str = "My Library") -> ObjectId:
    db = get_db()
    now = utcnow()
    ws = await db.workspaces.insert_one({"name": name, "owner_id": user_id, "kind": "personal", "created_at": now, "updated_at": now})
    await db.workspace_memberships.insert_one({"workspace_id": ws.inserted_id, "user_id": user_id, "role": "owner", "created_at": now})
    await db.users.update_one({"_id": user_id}, {"$set": {"default_workspace_id": ws.inserted_id}})
    return ws.inserted_id


async def create_user(
    email: str,
    password: str | None,
    *,
    display_name: str | None = None,
    email_verified: bool = False,
) -> dict[str, Any]:
    s = get_settings()
    email = validate_email(email)
    if password is not None:
        problems = password_problems(password, s.password_min_length)
        if problems:
            raise ValidationFailed(problems[0], details={"field": "password", "problems": problems})
    now = utcnow()
    doc: dict[str, Any] = {
        "email": email,
        "email_normalized": normalize_email(email),
        "display_name": (display_name or email.split("@")[0])[:80],
        "password_hash": await hash_password(password) if password else None,
        "role": "USER",
        "status": "active",
        "email_verified_at": now if email_verified else None,
        "preferences": {"theme": "system"},
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
    }
    try:
        res = await get_db().users.insert_one(doc)
    except DuplicateKeyError as exc:
        raise Conflict(
            "We couldn't create an account with these details. If you already have an account, sign in or reset your password.",
            code="registration_unavailable",
        ) from exc
    doc["_id"] = res.inserted_id
    doc["default_workspace_id"] = await create_personal_workspace(res.inserted_id)
    await audit.record("user.registered", actor_id=res.inserted_id, target_type="user", target_id=res.inserted_id)
    return doc


async def authenticate(email: str, password: str) -> dict[str, Any]:
    user = await get_db().users.find_one({"email_normalized": normalize_email(email)})
    ok = await verify_password(user.get("password_hash") if user else None, password)
    if not user or not ok:
        raise Unauthorized("Invalid email or password.", code="invalid_credentials")
    if user["status"] == "suspended":
        raise Forbidden("This account is suspended. Contact support.", code="account_suspended")
    if user["status"] == "deleting":
        raise Unauthorized("Invalid email or password.", code="invalid_credentials")
    if needs_rehash(user["password_hash"]):
        await get_db().users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": await hash_password(password)}})
    return user


async def issue_session(user_id: ObjectId, *, ip: str | None, user_agent: str | None, reauthenticated: bool = True) -> IssuedSession:
    s = get_settings()
    token, csrf = new_token(32), new_token(24)
    now = utcnow()
    expires = now + timedelta(hours=s.session_ttl_hours)
    res = await get_db().sessions.insert_one(
        {
            "user_id": user_id,
            "token_hash": token_hash(token),
            "csrf_hash": token_hash(csrf, purpose="csrf"),
            "created_at": now,
            "last_seen_at": now,
            "rotated_at": now,
            "reauth_at": now if reauthenticated else None,
            "expires_at": expires,
            "revoked_at": None,
            "user_agent": (user_agent or "")[:300],
            "ip_prefix": _ip_prefix(ip),
        }
    )
    await get_db().users.update_one({"_id": user_id}, {"$set": {"last_login_at": now}})
    return IssuedSession(token, csrf, res.inserted_id, expires)


def _ip_prefix(ip: str | None) -> str | None:
    """Store only a coarse network prefix for the session list (privacy)."""
    if not ip:
        return None
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".0/24" if len(parts) == 4 else None


async def resolve_session(token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return (session, user) for a valid session token, else None."""
    s = get_settings()
    db = get_db()
    sess = await db.sessions.find_one({"token_hash": token_hash(token)})
    if not sess or sess.get("revoked_at"):
        return None
    now = utcnow()
    if require_utc(sess["expires_at"]) <= now:
        return None
    if require_utc(sess["last_seen_at"]) + timedelta(hours=s.session_idle_timeout_hours) <= now:
        await db.sessions.update_one({"_id": sess["_id"]}, {"$set": {"revoked_at": now, "revoke_reason": "idle"}})
        return None
    user = await db.users.find_one({"_id": sess["user_id"]})
    if not user or user["status"] != "active":
        return None
    if require_utc(sess["last_seen_at"]) + timedelta(minutes=1) <= now:
        await db.sessions.update_one({"_id": sess["_id"]}, {"$set": {"last_seen_at": now}})
    return sess, user


async def rotate_session_if_due(sess: dict[str, Any]) -> IssuedSession | None:
    """Rotate long-lived session tokens periodically. The old token is revoked atomically."""
    s = get_settings()
    now = utcnow()
    if require_utc(sess["rotated_at"]) + timedelta(minutes=s.session_rotate_after_minutes) > now:
        return None
    db = get_db()
    claimed = await db.sessions.find_one_and_update(
        {"_id": sess["_id"], "revoked_at": None, "rotated_at": sess["rotated_at"]},
        {"$set": {"revoked_at": now, "revoke_reason": "rotated"}},
    )
    if not claimed:
        return None  # a concurrent request already rotated it
    token, csrf = new_token(32), new_token(24)
    res = await db.sessions.insert_one(
        {
            **{k: v for k, v in sess.items() if k not in ("_id", "revoked_at", "revoke_reason")},
            "token_hash": token_hash(token),
            "csrf_hash": token_hash(csrf, purpose="csrf"),
            "rotated_at": now,
            "last_seen_at": now,
            "revoked_at": None,
            "rotated_from": sess["_id"],
        }
    )
    return IssuedSession(token, csrf, res.inserted_id, sess["expires_at"])


async def revoke_session(session_id: ObjectId, user_id: ObjectId, reason: str = "logout") -> bool:
    res = await get_db().sessions.update_one(
        {"_id": session_id, "user_id": user_id, "revoked_at": None},
        {"$set": {"revoked_at": utcnow(), "revoke_reason": reason}},
    )
    return res.modified_count == 1


async def revoke_all_sessions(user_id: ObjectId, *, except_id: ObjectId | None = None, reason: str = "logout_all") -> int:
    q: dict[str, Any] = {"user_id": user_id, "revoked_at": None}
    if except_id:
        q["_id"] = {"$ne": except_id}
    res = await get_db().sessions.update_many(q, {"$set": {"revoked_at": utcnow(), "revoke_reason": reason}})
    return res.modified_count


async def mark_reauthenticated(session_id: ObjectId) -> None:
    await get_db().sessions.update_one({"_id": session_id}, {"$set": {"reauth_at": utcnow()}})


def recently_authenticated(sess: dict[str, Any]) -> bool:
    ra = ensure_utc(sess.get("reauth_at"))
    return bool(ra and ra + timedelta(minutes=get_settings().reauth_window_minutes) > utcnow())


# --- Email verification -----------------------------------------------------------------


async def send_verification(user: dict[str, Any]) -> bool:
    token = new_token(32)
    await get_db().email_verification_tokens.insert_one(
        {
            "user_id": user["_id"],
            "token_hash": token_hash(token, purpose="verify"),
            "expires_at": utcnow() + timedelta(hours=24),
            "used_at": None,
            "created_at": utcnow(),
        }
    )
    link = f"{get_settings().app_public_url}/verify-email?token={token}"
    res = await send_email(
        user["email"], "Verify your Knowledge Studio email", f"Confirm your email address:\n\n{link}\n\nThis link expires in 24 hours."
    )
    return res.delivered


async def consume_verification(token: str) -> ObjectId:
    now = utcnow()
    rec = await get_db().email_verification_tokens.find_one_and_update(
        {"token_hash": token_hash(token, purpose="verify"), "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}},
    )
    if not rec:
        raise ValidationFailed("This verification link is invalid or has expired.", code="invalid_token")
    await get_db().users.update_one({"_id": rec["user_id"]}, {"$set": {"email_verified_at": now, "updated_at": now}})
    await audit.record("user.email_verified", actor_id=rec["user_id"], target_type="user", target_id=rec["user_id"])
    return rec["user_id"]


# --- Password reset ---------------------------------------------------------------------


async def request_password_reset(email: str) -> None:
    """Always behaves identically whether or not the address is registered."""
    user = await get_db().users.find_one({"email_normalized": normalize_email(email), "status": "active"})
    if not user:
        return
    token = new_token(32)
    await get_db().password_reset_tokens.insert_one(
        {
            "user_id": user["_id"],
            "token_hash": token_hash(token, purpose="reset"),
            "expires_at": utcnow() + timedelta(minutes=30),
            "used_at": None,
            "created_at": utcnow(),
        }
    )
    link = f"{get_settings().app_public_url}/reset-password?token={token}"
    await send_email(
        user["email"],
        "Reset your Knowledge Studio password",
        f"Reset your password:\n\n{link}\n\nThis link expires in 30 minutes. If you didn't request it, ignore this email.",
    )
    await audit.record("user.password_reset_requested", actor_id=user["_id"], target_type="user", target_id=user["_id"])


async def reset_password(token: str, new_password: str) -> ObjectId:
    problems = password_problems(new_password, get_settings().password_min_length)
    if problems:
        raise ValidationFailed(problems[0], details={"field": "password"})
    now = utcnow()
    rec = await get_db().password_reset_tokens.find_one_and_update(
        {"token_hash": token_hash(token, purpose="reset"), "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}},
    )
    if not rec:
        raise ValidationFailed("This reset link is invalid or has expired.", code="invalid_token")
    await get_db().users.update_one(
        {"_id": rec["user_id"]}, {"$set": {"password_hash": await hash_password(new_password), "updated_at": now}}
    )
    await revoke_all_sessions(rec["user_id"], reason="password_reset")
    await audit.record("user.password_reset", actor_id=rec["user_id"], target_type="user", target_id=rec["user_id"])
    return rec["user_id"]


async def change_password(user: dict[str, Any], current: str | None, new_password: str, keep_session: ObjectId) -> None:
    if user.get("password_hash"):
        if not current or not await verify_password(user["password_hash"], current):
            raise Unauthorized("Current password is incorrect.", code="invalid_credentials")
    problems = password_problems(new_password, get_settings().password_min_length)
    if problems:
        raise ValidationFailed(problems[0], details={"field": "password"})
    await get_db().users.update_one(
        {"_id": user["_id"]}, {"$set": {"password_hash": await hash_password(new_password), "updated_at": utcnow()}}
    )
    await revoke_all_sessions(user["_id"], except_id=keep_session, reason="password_changed")
    await audit.record("user.password_changed", actor_id=user["_id"], target_type="user", target_id=user["_id"])
