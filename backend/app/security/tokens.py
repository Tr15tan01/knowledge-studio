"""Opaque random tokens. Only keyed hashes are persisted."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.config import get_settings


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str, *, purpose: str = "session") -> str:
    """Keyed hash (HMAC-SHA256) so a database leak alone cannot be used to forge lookups."""
    key = get_settings().session_secret.get_secret_value().encode() or b"dev-only-unset-secret"
    return hmac.new(key, f"{purpose}:{token}".encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
