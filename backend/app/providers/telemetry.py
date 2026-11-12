"""Provider call accounting: tokens, latency, retries, failures and estimated cost.

Costs are *estimates* from an admin-editable price table (``system_settings`` id
``model_prices``); they are labelled as estimates everywhere they are displayed. Provider
invoices remain authoritative.
"""

from __future__ import annotations

from typing import Any

from app.core.timeutil import utcnow
from app.database.client import get_db

# USD per 1M tokens. Placeholder defaults: verify against the provider's current price list
# and adjust in Admin → Usage & costs → Model prices.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "default": {"input": 0.30, "output": 2.50},
    "embedding": {"input": 0.15, "output": 0.0},
}

COMPUTE_OUTPUT_WEIGHT = 4  # compute units weight output tokens more heavily than input


async def price_table() -> dict[str, dict[str, float]]:
    doc = await get_db().system_settings.find_one({"_id": "model_prices"})
    return {**DEFAULT_PRICES, **((doc or {}).get("prices") or {})}


async def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int, *, kind: str = "generate") -> float:
    prices = await price_table()
    p = prices.get(model) or prices.get("embedding" if kind == "embed" else "default") or DEFAULT_PRICES["default"]
    return round(input_tokens / 1_000_000 * p.get("input", 0) + output_tokens / 1_000_000 * p.get("output", 0), 8)


def compute_units(input_tokens: int, output_tokens: int) -> int:
    """1 compute unit = 1,000 weighted tokens (output weighted x4). Rounded up, min 1 for any call."""
    weighted = input_tokens + COMPUTE_OUTPUT_WEIGHT * output_tokens
    return max(1, -(-weighted // 1000)) if weighted else 0


async def record_call(
    *,
    provider: str,
    model: str,
    operation: str,
    kind: str,
    account_id: Any = None,
    workspace_id: Any = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: float = 0,
    status: str = "ok",
    retries: int = 0,
    error_code: str | None = None,
    job_id: Any = None,
    tokens_estimated: bool = False,
) -> dict[str, Any]:
    cost = await estimate_cost_usd(model, input_tokens, output_tokens, kind=kind)
    doc = {
        "provider": provider,
        "model": model,
        "operation": operation,
        "kind": kind,
        "account_id": account_id,
        "workspace_id": workspace_id,
        "job_id": job_id,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "tokens_estimated": tokens_estimated,
        "latency_ms": round(latency_ms, 1),
        "status": status,
        "retries": retries,
        "error_code": error_code,
        "estimated_cost_usd": cost,
        "compute_units": compute_units(input_tokens, output_tokens),
        "occurred_at": utcnow(),
    }
    await get_db().provider_calls.insert_one(doc)
    return doc
