"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.database.client import mongo
from app.observability.health import readiness
from app.observability.logging import configure_logging, request_id_var, user_ref_var

log = logging.getLogger("app")

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_MAX_BODY = 1 * 1024 * 1024  # JSON bodies; uploads have their own streaming limits
ORIGIN_EXEMPT_PREFIXES = ("/api/v1/billing/webhooks/",)


def _init_sentry() -> None:
    s = get_settings()
    dsn = s.sentry_dsn.get_secret_value()
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=dsn, environment=s.app_env.value, traces_sample_rate=s.sentry_traces_sample_rate, send_default_pii=False)
    except ImportError:
        log.warning("sentry_sdk_not_installed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    problems = s.validate_for_startup()
    if problems:
        for p in problems:
            log.error("configuration_problem", extra={"problem": p})
        if s.is_production or not s.mongodb_uri.get_secret_value():
            raise RuntimeError("Invalid configuration: " + "; ".join(problems))
    _init_sentry()
    await mongo.connect(s)
    if not s.is_production:
        from app.billing.catalog import bootstrap_price_mappings, seed_catalog
        from app.database.setup import setup_database

        await setup_database(mongo.db)
        await seed_catalog()
        await bootstrap_price_mappings()
    log.info("api_started", extra={"env": s.app_env.value, "transactions_supported": mongo.transactions_supported})
    worker, worker_task = None, None
    if s.run_worker_in_api:
        from app.jobs.runtime import Worker

        worker = Worker()
        worker_task = asyncio.create_task(worker.run())
    yield
    if worker and worker_task:
        # Give running jobs a moment to reach a checkpoint; unfinished ones are recovered
        # through lease expiry when the service starts again.
        worker.stop()
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except TimeoutError:
            pass
    await mongo.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Knowledge Studio API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if not s.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)

    allowed_origins = set(s.cors_origins) | {s.app_public_url.rstrip("/")}

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        if len(rid) > 64 or not rid.replace("-", "").isalnum():
            rid = uuid.uuid4().hex
        request.state.request_id = rid
        token = request_id_var.set(rid)
        user_ref_var.set(None)
        try:
            if request.method in UNSAFE and not request.url.path.startswith(ORIGIN_EXEMPT_PREFIXES):
                origin = request.headers.get("origin")
                if origin and origin.rstrip("/") not in allowed_origins:
                    return JSONResponse(
                        {"error": {"code": "origin_rejected", "message": "Cross-origin request rejected.", "request_id": rid}},
                        status_code=403,
                    )
                cl = request.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > DEFAULT_MAX_BODY and not request.url.path.endswith("/files/upload"):
                    return JSONResponse(
                        {"error": {"code": "payload_too_large", "message": "Request body is too large.", "request_id": rid}},
                        status_code=413,
                    )
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if s.is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        if request.url.path.startswith("/api/v1/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-csrf-token", "x-workspace-id", "idempotency-key", "x-request-id", "last-event-id"],
        expose_headers=["x-request-id"],
        max_age=600,
    )

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    async def ready() -> JSONResponse:
        r = await readiness(deep=False)
        return JSONResponse(r, status_code=200 if r["ready"] else 503)

    from app.api.v1 import router as v1

    app.include_router(v1, prefix="/api/v1")
    return app


app = create_app()
