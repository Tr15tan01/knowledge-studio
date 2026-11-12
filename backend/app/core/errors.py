"""Application error model and consistent JSON error responses.

Every error returned to clients has the shape::

    {"error": {"code": "quota_exceeded", "message": "...", "details": {...}, "request_id": "..."}}

Stack traces are never returned to clients.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}
        self.retryable = retryable


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class QuotaExceeded(AppError):
    status_code = 402
    code = "quota_exceeded"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"

    def __init__(self, message: str, **kw: Any) -> None:
        kw.setdefault("retryable", True)
        super().__init__(message, **kw)


class ProviderError(AppError):
    """An upstream AI/billing/storage provider failed."""

    status_code = 502
    code = "provider_error"


def _body(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.warning("app_error", extra={"code": exc.code, "status": exc.status_code, "error_message": exc.message})
        body = _body(request, exc.code, exc.message, exc.details)
        body["error"]["retryable"] = exc.retryable
        headers = {}
        if isinstance(exc, RateLimited) and "retry_after" in exc.details:
            headers["Retry-After"] = str(int(exc.details["retry_after"]))
        return JSONResponse(body, status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(_body(request, "validation_failed", "Request validation failed.", {"errors": errors}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized", 403: "forbidden"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(request, code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", extra={"path": request.url.path})
        return JSONResponse(_body(request, "internal_error", "An unexpected error occurred. Please try again."), status_code=500)
