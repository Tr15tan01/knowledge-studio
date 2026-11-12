from __future__ import annotations

from fastapi import Response

from app.auth.service import IssuedSession
from app.core.config import get_settings


def set_session_cookies(response: Response, issued: IssuedSession) -> None:
    s = get_settings()
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(
        s.session_cookie_name,
        issued.token,
        max_age=max_age,
        httponly=True,
        secure=bool(s.cookie_secure),
        samesite="lax",
        path="/",
        domain=s.cookie_domain or None,
    )
    # Readable by the frontend so it can echo it in X-CSRF-Token; it is useless without the
    # HttpOnly session cookie and is bound to the session server-side.
    response.set_cookie(
        s.csrf_cookie_name,
        issued.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=bool(s.cookie_secure),
        samesite="lax",
        path="/",
        domain=s.cookie_domain or None,
    )


def clear_session_cookies(response: Response) -> None:
    s = get_settings()
    for name in (s.session_cookie_name, s.csrf_cookie_name):
        response.delete_cookie(name, path="/", domain=s.cookie_domain or None)
