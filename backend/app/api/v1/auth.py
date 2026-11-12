from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field

from app.auth import google, service
from app.auth.cookies import clear_session_cookies, set_session_cookies
from app.auth.deps import Auth, client_ip, optional_auth
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.database.ids import oid
from app.schemas.common import APIModel, iso
from app.security import ratelimit
from app.services import audit
from app.services.system_settings import get_runtime_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(APIModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    display_name: str | None = Field(default=None, max_length=80)


class LoginIn(APIModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class TokenIn(APIModel):
    token: str = Field(min_length=10, max_length=200)


class EmailIn(APIModel):
    email: str = Field(max_length=320)


class ResetIn(APIModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=256)


class PasswordIn(APIModel):
    password: str = Field(max_length=256)


class ChangePasswordIn(APIModel):
    current_password: str | None = Field(default=None, max_length=256)
    new_password: str = Field(max_length=256)


def user_out(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "display_name": user.get("display_name"),
        "role": user.get("role", "USER"),
        "email_verified": bool(user.get("email_verified_at")),
        "has_password": bool(user.get("password_hash")),
        "default_workspace_id": str(user["default_workspace_id"]) if user.get("default_workspace_id") else None,
        "preferences": user.get("preferences", {}),
        "created_at": iso(user.get("created_at")),
    }


@router.get("/config")
async def auth_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "google_enabled": s.google_auth_enabled,
        "registration_enabled": (await get_runtime_settings()).registration_enabled,
        "email_verification_required": s.email_verification_required,
        "email_delivery_configured": s.email_provider == "smtp",
        "password_min_length": s.password_min_length,
    }


@router.post("/register", status_code=201)
async def register(body: RegisterIn, request: Request, response: Response) -> dict[str, Any]:
    s = get_settings()
    await ratelimit.hit("register", client_ip(request) or "?", limit=s.rate_limit_register_per_hour, window_seconds=3600)
    if not (await get_runtime_settings()).registration_enabled:
        raise Forbidden("New sign-ups are closed at the moment.", code="registration_closed")
    if s.email_verification_required:
        # Uniform response regardless of whether the address already exists.
        try:
            user = await service.create_user(body.email, body.password, display_name=body.display_name)
            delivered = await service.send_verification(user)
        except Exception as exc:  # noqa: BLE001
            from app.core.errors import Conflict

            if not isinstance(exc, Conflict):
                raise
            delivered = s.email_provider == "smtp"
        return {
            "status": "verification_required",
            "message": "If this address can be registered, we've sent a verification link.",
            "email_delivery_configured": s.email_provider == "smtp",
            "delivered": delivered if s.email_provider != "smtp" else None,
        }
    user = await service.create_user(body.email, body.password, display_name=body.display_name)
    issued = await service.issue_session(user["_id"], ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    set_session_cookies(response, issued)
    return {"status": "signed_in", "user": user_out(user)}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    s = get_settings()
    ip = client_ip(request) or "?"
    await ratelimit.hit("login_ip", ip, limit=s.rate_limit_login_per_15m * 3, window_seconds=900)
    await ratelimit.hit("login_email", service.normalize_email(body.email), limit=s.rate_limit_login_per_15m, window_seconds=900)
    try:
        user = await service.authenticate(body.email, body.password)
    except Exception:
        await audit.record("auth.login_failed", actor_type="user", details={"email_domain": body.email.split("@")[-1][:100]}, ip=ip)
        raise
    issued = await service.issue_session(user["_id"], ip=ip, user_agent=request.headers.get("user-agent"))
    set_session_cookies(response, issued)
    await audit.record("auth.login", actor_id=user["_id"], target_type="user", target_id=user["_id"], ip=ip)
    return {"status": "signed_in", "user": user_out(user)}


@router.post("/logout")
async def logout(auth: Auth, response: Response) -> dict[str, str]:
    await service.revoke_session(auth.session["_id"], auth.user_id)
    clear_session_cookies(response)
    return {"status": "signed_out"}


@router.get("/session")
async def session(request: Request, response: Response) -> dict[str, Any]:
    auth = await optional_auth(request)
    if not auth:
        return {"authenticated": False}
    rotated = await service.rotate_session_if_due(auth.session)
    if rotated:
        set_session_cookies(response, rotated)
    return {"authenticated": True, "user": user_out(auth.user), "recently_authenticated": service.recently_authenticated(auth.session)}


@router.post("/verify-email")
async def verify_email(body: TokenIn) -> dict[str, str]:
    await service.consume_verification(body.token)
    return {"status": "verified"}


@router.post("/resend-verification")
async def resend_verification(auth: Auth) -> dict[str, Any]:
    await ratelimit.hit("verify_resend", str(auth.user_id), limit=5, window_seconds=3600)
    if auth.user.get("email_verified_at"):
        return {"status": "already_verified"}
    delivered = await service.send_verification(auth.user)
    return {"status": "sent" if delivered else "not_delivered", "email_delivery_configured": get_settings().email_provider == "smtp"}


@router.post("/password/forgot")
async def forgot_password(body: EmailIn, request: Request) -> dict[str, Any]:
    s = get_settings()
    await ratelimit.hit("pw_reset_ip", client_ip(request) or "?", limit=s.rate_limit_password_reset_per_hour * 4, window_seconds=3600)
    await ratelimit.hit(
        "pw_reset_email", service.normalize_email(body.email), limit=s.rate_limit_password_reset_per_hour, window_seconds=3600
    )
    await service.request_password_reset(body.email)
    return {
        "status": "accepted",
        "message": "If an account exists for this address, a reset link has been sent.",
        "email_delivery_configured": s.email_provider == "smtp",
    }


@router.post("/password/reset")
async def password_reset(body: ResetIn, request: Request) -> dict[str, str]:
    await ratelimit.hit("pw_reset_confirm", client_ip(request) or "?", limit=20, window_seconds=3600)
    await service.reset_password(body.token, body.password)
    return {"status": "password_reset"}


@router.post("/password/change")
async def change_password(body: ChangePasswordIn, auth: Auth) -> dict[str, str]:
    await ratelimit.hit("pw_change", str(auth.user_id), limit=10, window_seconds=3600)
    await service.change_password(auth.user, body.current_password, body.new_password, auth.session["_id"])
    return {"status": "password_changed"}


@router.post("/reauthenticate")
async def reauthenticate(body: PasswordIn, auth: Auth) -> dict[str, str]:
    await ratelimit.hit("reauth", str(auth.user_id), limit=10, window_seconds=900)
    from app.security.passwords import verify_password

    if not auth.user.get("password_hash"):
        raise ValidationFailed("Set a password first to confirm sensitive actions.", code="no_password")
    if not await verify_password(auth.user["password_hash"], body.password):
        await audit.record("auth.reauth_failed", actor_id=auth.user_id)
        raise Forbidden("Password is incorrect.", code="invalid_credentials")
    await service.mark_reauthenticated(auth.session["_id"])
    return {"status": "reauthenticated"}


@router.get("/sessions")
async def list_sessions(auth: Auth) -> dict[str, Any]:
    now = utcnow()
    items = []
    async for s in (
        get_db().sessions.find({"user_id": auth.user_id, "revoked_at": None, "expires_at": {"$gt": now}}).sort("last_seen_at", -1).limit(50)
    ):
        items.append(
            {
                "id": str(s["_id"]),
                "current": s["_id"] == auth.session["_id"],
                "created_at": iso(s["created_at"]),
                "last_seen_at": iso(s["last_seen_at"]),
                "user_agent": s.get("user_agent"),
                "network": s.get("ip_prefix"),
            }
        )
    return {"items": items}


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, auth: Auth) -> dict[str, str]:
    if not await service.revoke_session(oid(session_id, what="session"), auth.user_id, reason="user_revoked"):
        raise NotFound("Session not found.")
    await audit.record("auth.session_revoked", actor_id=auth.user_id, target_type="session", target_id=session_id)
    return {"status": "revoked"}


@router.post("/sessions/revoke-others")
async def revoke_others(auth: Auth) -> dict[str, int]:
    n = await service.revoke_all_sessions(auth.user_id, except_id=auth.session["_id"], reason="user_revoked_others")
    await audit.record("auth.sessions_revoked_others", actor_id=auth.user_id, details={"count": n})
    return {"revoked": n}


# --- Google OAuth -------------------------------------------------------------------------


@router.get("/google/start")
async def google_start(request: Request, next: str = Query("/app", max_length=200), mode: str = Query("login")) -> RedirectResponse:
    link_user = None
    if mode == "link":
        auth = await optional_auth(request)
        if not auth:
            raise Forbidden("Sign in before linking a Google account.")
        link_user = auth.user_id
    url = await google.start(mode="link" if link_user else "login", link_user_id=link_user, next_path=next)
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    google._require_enabled()
    s = get_settings()
    base = s.app_public_url

    def fail(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{base}/sign-in?error={reason}", status_code=302)

    if error or not code or not state:
        return fail("google_cancelled")
    try:
        st = await google.consume_state(state)
        claims = await google.exchange_code(code, st["verifier"], st["nonce"])
    except ValidationFailed as exc:
        return fail(exc.code)
    db = get_db()
    sub = claims["sub"]
    linked = await db.oauth_accounts.find_one({"provider": "google", "subject": sub})
    if st["mode"] == "link":
        if linked and linked["user_id"] != st["link_user_id"]:
            return RedirectResponse(f"{base}/app/settings?error=google_already_linked", status_code=302)
        if not linked:
            await db.oauth_accounts.insert_one(
                {"provider": "google", "subject": sub, "user_id": st["link_user_id"], "email": claims.get("email"), "created_at": utcnow()}
            )
            await audit.record("auth.google_linked", actor_id=st["link_user_id"], target_type="user", target_id=st["link_user_id"])
        return RedirectResponse(f"{base}/app/settings?linked=google", status_code=302)

    if linked:
        user = await db.users.find_one({"_id": linked["user_id"], "status": "active"})
        if not user:
            return fail("account_unavailable")
    else:
        email = claims.get("email")
        if not email or not claims.get("email_verified"):
            return fail("google_email_unverified")
        if await db.users.find_one({"email_normalized": service.normalize_email(email)}):
            # Never merge automatically: the owner must sign in and link from Settings.
            return fail("account_exists_link_from_settings")
        if not (await get_runtime_settings()).registration_enabled:
            return fail("registration_closed")
        user = await service.create_user(email, None, display_name=claims.get("name"), email_verified=True)
        await db.oauth_accounts.insert_one(
            {"provider": "google", "subject": sub, "user_id": user["_id"], "email": email, "created_at": utcnow()}
        )
    issued = await service.issue_session(user["_id"], ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    resp = RedirectResponse(f"{base}{st.get('next') or '/app'}", status_code=302)
    set_session_cookies(resp, issued)
    await audit.record("auth.login", actor_id=user["_id"], details={"method": "google"})
    return resp
