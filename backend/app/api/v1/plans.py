"""Public pricing catalog (read-only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.billing.catalog import list_plans
from app.billing.schema import FEATURE_KEYS, FEATURE_LABELS
from app.core.config import get_settings
from app.core.units import format_bytes
from app.database.client import get_db

router = APIRouter(prefix="/plans", tags=["plans"])


def public_plan(plan: dict[str, Any], mappings: dict[str, Any] | None = None) -> dict[str, Any]:
    v = plan["version_doc"]
    e = v["entitlements"]
    pricing = v["pricing"]
    mappings = mappings or {}
    return {
        "id": str(plan["_id"]),
        "slug": plan["slug"],
        "name": v.get("name") or plan["name"],
        "description": v.get("description", ""),
        "tagline": (v.get("marketing") or {}).get("tagline", ""),
        "highlights": (v.get("marketing") or {}).get("highlights", []),
        "is_default": plan.get("is_default", False),
        "version": v["version"],
        "currency": pricing.get("currency", "USD"),
        "monthly_price_minor": pricing.get("monthly_display_price_minor"),
        "annual_price_minor": pricing.get("annual_display_price_minor"),
        "checkout_available": {
            "month": bool(mappings.get("month")) and get_settings().paddle_enabled,
            "year": bool(mappings.get("year")) and get_settings().paddle_enabled,
        },
        "limits": {
            "uploads_per_period": e["upload_limit_per_period"],
            "storage_bytes": e["storage_limit_bytes"],
            "storage_display": format_bytes(e["storage_limit_bytes"]),
            "max_file_size_bytes": e["max_file_size_bytes"],
            "max_file_size_display": format_bytes(e["max_file_size_bytes"]),
            "questions_per_period": e["question_limit_per_period"],
            "research_reports_per_period": e["research_report_limit_per_period"],
            "concurrent_research_jobs": e["max_concurrent_research_jobs"],
            "saved_research_projects": e["max_saved_research_projects"],
        },
        "features": [{"key": k, "label": FEATURE_LABELS[k], "enabled": bool(e["features"].get(k))} for k in FEATURE_KEYS],
    }


@router.get("")
async def pricing() -> dict[str, Any]:
    plans = await list_plans(public_only=True)
    out = []
    for p in plans:
        maps = {m["interval"]: m async for m in get_db().price_mappings.find({"plan_id": p["_id"], "active": True})}
        out.append(public_plan(p, maps))
    return {
        "plans": out,
        "billing_enabled": get_settings().paddle_enabled,
        "notes": [
            "Quotas reset monthly. Storage counts original uploaded files only, in binary units (1 GiB = 1,073,741,824 bytes).",
            "AI features are subject to fair-use compute limits. Processing times are not guaranteed.",
        ],
    }
