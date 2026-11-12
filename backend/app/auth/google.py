"""Google OAuth 2.0 / OpenID Connect (authorization code flow + PKCE).

* ``state`` is random, single-use, stored hashed with a 10-minute TTL.
* PKCE (S256) protects the code exchange.
* ``nonce`` binds the ID token to this login attempt.
* The ID token is received directly from Google's token endpoint over TLS, which OIDC Core
  §3.1.3.7 permits in place of signature verification; we still validate ``iss``, ``aud``,
  ``exp``, ``iat`` and ``nonce``.
* Accounts are never merged automatically by email. An existing account links Google from
  Settings while signed in (``mode=link``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from bson import ObjectId

from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.security.tokens import new_token, token_hash

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")


def _require_enabled() -> None:
    if not get_settings().google_auth_enabled:
        raise NotFound("Not found.")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def start(*, mode: str = "login", link_user_id: ObjectId | None = None, next_path: str = "/app") -> str:
    _require_enabled()
    s = get_settings()
    state, verifier, nonce = new_token(32), new_token(48), new_token(16)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/app"
    await get_db().oauth_states.insert_one(
        {
            "state_hash": token_hash(state, purpose="oauth_state"),
            "verifier": verifier,
            "nonce": nonce,
            "mode": mode,
            "link_user_id": link_user_id,
            "next": next_path,
            "expires_at": utcnow() + timedelta(minutes=10),
            "created_at": utcnow(),
        }
    )
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _decode_jwt_payload(jwt: str) -> dict[str, Any]:
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed("Google sign-in failed.", code="oauth_invalid_token") from exc


async def consume_state(state: str) -> dict[str, Any]:
    rec = await get_db().oauth_states.find_one_and_delete(
        {"state_hash": token_hash(state, purpose="oauth_state"), "expires_at": {"$gt": utcnow()}}
    )
    if not rec:
        raise ValidationFailed("This sign-in attempt expired. Please try again.", code="oauth_state_invalid")
    return rec


async def exchange_code(code: str, verifier: str, nonce: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret.get_secret_value(),
                "redirect_uri": s.google_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    if r.status_code != 200:
        raise ValidationFailed("Google sign-in failed.", code="oauth_exchange_failed")
    claims = _decode_jwt_payload(r.json().get("id_token", ""))
    validate_claims(claims, nonce)
    return claims


def validate_claims(claims: dict[str, Any], nonce: str) -> None:
    s = get_settings()
    now = time.time()
    if claims.get("iss") not in ISSUERS:
        raise ValidationFailed("Google sign-in failed.", code="oauth_bad_issuer")
    aud = claims.get("aud")
    if aud != s.google_client_id and not (isinstance(aud, list) and s.google_client_id in aud):
        raise ValidationFailed("Google sign-in failed.", code="oauth_bad_audience")
    if float(claims.get("exp", 0)) < now - 30 or float(claims.get("iat", now)) > now + 300:
        raise ValidationFailed("Google sign-in failed.", code="oauth_expired")
    if claims.get("nonce") != nonce:
        raise ValidationFailed("Google sign-in failed.", code="oauth_bad_nonce")
    if not claims.get("sub"):
        raise ValidationFailed("Google sign-in failed.", code="oauth_no_subject")
