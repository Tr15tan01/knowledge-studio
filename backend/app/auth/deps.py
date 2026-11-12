"""Request authentication, CSRF enforcement, role checks and workspace authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from bson import ObjectId
from fastapi import Depends, Request

from app.auth.service import recently_authenticated, resolve_session
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.database.client import get_db
from app.database.ids import maybe_oid
from app.observability.logging import user_ref_var
from app.security.tokens import constant_time_equals, token_hash

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROLE_RANK = {"USER": 0, "ADMIN": 1, "SUPER_ADMIN": 2}


@dataclass
class AuthContext:
    user: dict[str, Any]
    session: dict[str, Any]

    @property
    def user_id(self) -> ObjectId:
        return self.user["_id"]

    @property
    def account_id(self) -> ObjectId:
        # Billing/entitlement account. Personal accounts today; team accounts later map
        # workspaces to an owning account without changing quota code.
        return self.user["_id"]

    @property
    def role(self) -> str:
        return self.user.get("role", "USER")

    def has_role(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[role]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _authenticate(request: Request) -> AuthContext:
    s = get_settings()
    token = request.cookies.get(s.session_cookie_name)
    if not token:
        raise Unauthorized("Sign in to continue.")
    resolved = await resolve_session(token)
    if not resolved:
        raise Unauthorized("Your session has expired. Please sign in again.", code="session_expired")
    sess, user = resolved
    if request.method not in SAFE_METHODS:
        header = request.headers.get("x-csrf-token", "")
        if not header or not constant_time_equals(token_hash(header, purpose="csrf"), sess["csrf_hash"]):
            raise Forbidden("Security check failed. Refresh the page and try again.", code="csrf_failed")
    if s.email_verification_required and not user.get("email_verified_at"):
        path = request.url.path
        if not any(path.startswith(p) for p in ("/api/v1/auth", "/api/v1/me")):
            raise Forbidden("Verify your email address to continue.", code="email_unverified")
    request.state.auth = AuthContext(user, sess)
    user_ref_var.set(str(user["_id"]))
    return request.state.auth


async def current_auth(request: Request) -> AuthContext:
    return await _authenticate(request)


async def optional_auth(request: Request) -> AuthContext | None:
    try:
        return await _authenticate(request)
    except (Unauthorized, Forbidden):
        return None


def require_role(role: str):
    async def dep(auth: Annotated[AuthContext, Depends(current_auth)]) -> AuthContext:
        if not auth.has_role(role):
            # 404 rather than 403 would hide admin routes; we use 403 with a neutral message.
            raise Forbidden("You don't have permission to perform this action.")
        return auth

    return dep


def require_recent_auth(auth: AuthContext) -> None:
    if not recently_authenticated(auth.session):
        raise Forbidden("Please confirm your password to continue with this sensitive action.", code="reauth_required")


Auth = Annotated[AuthContext, Depends(current_auth)]
AdminAuth = Annotated[AuthContext, Depends(require_role("ADMIN"))]
SuperAdminAuth = Annotated[AuthContext, Depends(require_role("SUPER_ADMIN"))]


@dataclass
class WorkspaceContext:
    auth: AuthContext
    workspace_id: ObjectId
    role: str

    @property
    def user_id(self) -> ObjectId:
        return self.auth.user_id

    @property
    def account_id(self) -> ObjectId:
        return self.auth.account_id

    def scope(self, **extra: Any) -> dict[str, Any]:
        """Mandatory tenant filter for every query on workspace-owned data."""
        return {"workspace_id": self.workspace_id, **extra}

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "editor")


async def workspace_context(request: Request, auth: Auth) -> WorkspaceContext:
    requested = request.headers.get("x-workspace-id") or request.query_params.get("workspace_id")
    ws_id = maybe_oid(requested) if requested else auth.user.get("default_workspace_id")
    if ws_id is None:
        raise NotFound("Workspace not found.")
    membership = await get_db().workspace_memberships.find_one({"workspace_id": ws_id, "user_id": auth.user_id})
    if not membership:
        raise NotFound("Workspace not found.")
    return WorkspaceContext(auth, ws_id, membership["role"])


Workspace = Annotated[WorkspaceContext, Depends(workspace_context)]
