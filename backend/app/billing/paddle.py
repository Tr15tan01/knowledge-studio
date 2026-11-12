"""Paddle Billing API client and webhook signature verification.

Paddle is authoritative for payments and subscription status. This client is server-side
only; the API key never reaches the browser (only the public client-side token does).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import ProviderError, ServiceUnavailable
from app.providers.resilience import CircuitBreaker, TransientError, with_retries

log = logging.getLogger("app.billing.paddle")

_breaker = CircuitBreaker("Paddle")


def api_base() -> str:
    return "https://api.paddle.com" if get_settings().paddle_environment == "production" else "https://sandbox-api.paddle.com"


def _require_enabled() -> str:
    s = get_settings()
    if not s.paddle_enabled:
        raise ServiceUnavailable(
            "Billing is disabled in this environment (PADDLE_ENABLED=0). No payments can be taken.",
            code="billing_disabled",
            retryable=False,
        )
    key = s.paddle_api_key.get_secret_value()
    if not key:
        raise ServiceUnavailable("Billing is not configured (PADDLE_API_KEY missing).", code="billing_not_configured", retryable=False)
    return key


async def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    key = _require_enabled()

    async def once() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=api_base(), timeout=20) as client:
                r = await client.request(
                    method, path, json=json_body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(type(exc).__name__) from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise TransientError(f"paddle {r.status_code}")
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
            except ValueError:
                err = {}
            raise ProviderError(
                f"Paddle rejected the request: {err.get('detail') or err.get('code') or r.status_code}",
                code="paddle_" + str(err.get("code") or r.status_code),
                status_code=502 if r.status_code >= 500 else 400,
                details={"paddle_code": err.get("code"), "status": r.status_code},
            )
        return r.json()

    return await with_retries(once, breaker=_breaker, timeout=30)


async def get_price(price_id: str) -> dict[str, Any]:
    return (await _request("GET", f"/prices/{price_id}"))["data"]


async def get_product(product_id: str) -> dict[str, Any]:
    return (await _request("GET", f"/products/{product_id}"))["data"]


async def get_subscription(subscription_id: str) -> dict[str, Any]:
    return (await _request("GET", f"/subscriptions/{subscription_id}"))["data"]


async def create_customer(email: str, name: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"email": email}
    if name:
        body["name"] = name[:100]
    return (await _request("POST", "/customers", json_body=body))["data"]


async def list_customers_by_email(email: str) -> list[dict[str, Any]]:
    from urllib.parse import quote

    return (await _request("GET", f"/customers?email={quote(email)}"))["data"]


async def create_transaction(*, price_id: str, customer_id: str | None, custom_data: dict[str, str]) -> dict[str, Any]:
    body: dict[str, Any] = {"items": [{"price_id": price_id, "quantity": 1}], "custom_data": custom_data, "collection_mode": "automatic"}
    if customer_id:
        body["customer_id"] = customer_id
    return (await _request("POST", "/transactions", json_body=body))["data"]


async def portal_session(customer_id: str, subscription_ids: list[str]) -> dict[str, Any]:
    return (await _request("POST", f"/customers/{customer_id}/portal-sessions", json_body={"subscription_ids": subscription_ids[:25]}))[
        "data"
    ]


async def preview_subscription_change(subscription_id: str, price_id: str) -> dict[str, Any]:
    body = {"items": [{"price_id": price_id, "quantity": 1}], "proration_billing_mode": "prorated_immediately"}
    return (await _request("PATCH", f"/subscriptions/{subscription_id}/preview", json_body=body))["data"]


async def change_subscription(subscription_id: str, price_id: str) -> dict[str, Any]:
    body = {"items": [{"price_id": price_id, "quantity": 1}], "proration_billing_mode": "prorated_immediately"}
    return (await _request("PATCH", f"/subscriptions/{subscription_id}", json_body=body))["data"]


async def cancel_at_period_end(subscription_id: str) -> dict[str, Any]:
    return (await _request("POST", f"/subscriptions/{subscription_id}/cancel", json_body={"effective_from": "next_billing_period"}))["data"]


async def remove_scheduled_change(subscription_id: str) -> dict[str, Any]:
    return (await _request("PATCH", f"/subscriptions/{subscription_id}", json_body={"scheduled_change": None}))["data"]


async def health() -> tuple[bool, str]:
    s = get_settings()
    if not s.paddle_enabled:
        return True, "disabled"
    if not s.paddle_api_key.get_secret_value():
        return False, "API key missing"
    try:
        await _request("GET", "/event-types")
        return True, f"{s.paddle_environment} API reachable"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


# --- Webhook signature verification --------------------------------------------------------


class SignatureError(Exception):
    pass


def verify_signature(raw_body: bytes, header: str | None, secret: str, *, tolerance_seconds: int, now: float | None = None) -> int:
    """Verify ``Paddle-Signature: ts=<unix>;h1=<hex hmac>`` over ``ts:raw_body``.

    Mirrors the official SDK verifier: HMAC-SHA256 with the destination's secret key,
    constant-time comparison, any ``h1`` may match (secret rotation), and the timestamp must
    be within ``tolerance_seconds`` (replay protection; duplicate event ids are additionally
    rejected by the idempotency store).
    """
    if not secret:
        raise SignatureError("webhook secret not configured")
    if not header:
        raise SignatureError("missing signature header")
    parts: dict[str, list[str]] = {}
    for item in header.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())
    ts_list, sigs = parts.get("ts"), parts.get("h1") or []
    if not ts_list or not ts_list[0].isdigit() or not sigs:
        raise SignatureError("malformed signature header")
    ts = int(ts_list[0])
    now = time.time() if now is None else now
    if abs(now - ts) > tolerance_seconds:
        raise SignatureError("timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{ts}:".encode() + raw_body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise SignatureError("signature mismatch")
    return ts


def sign_for_tests(raw_body: bytes, secret: str, ts: int | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    return f"ts={ts};h1=" + hmac.new(secret.encode(), f"{ts}:".encode() + raw_body, hashlib.sha256).hexdigest()
