"""Admin-editable, validated non-secret policy settings.

Secrets are never stored here; environment variables stay authoritative for them.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.config import get_settings
from app.core.timeutil import utcnow
from app.database.client import get_db

log = logging.getLogger("app.settings")

OPERATION_CATEGORIES = (
    "simple_question",
    "standard_question",
    "cross_document_question",
    "deep_research",
    "graph_exploration",
    "document_extraction",
)


class QuotaPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_quota_restore_on_delete: bool = False
    replacement_counts_as_new_upload: Literal[True] = True
    storage_warning_thresholds: list[int] = Field(default_factory=lambda: [80, 90, 100])
    question_credit_mode: Literal["count", "credits"] = "count"
    operation_credits: dict[str, int] = Field(
        default_factory=lambda: {
            "simple_question": 1,
            "standard_question": 1,
            "cross_document_question": 2,
            "deep_research": 0,  # deep research uses the separate report allowance
            "graph_exploration": 0,
            "document_extraction": 0,
        }
    )
    failed_operation_policy: Literal["release"] = "release"
    over_limit_policy: Literal["block_new_usage"] = "block_new_usage"

    @field_validator("storage_warning_thresholds")
    @classmethod
    def _thr(cls, v: list[int]) -> list[int]:
        v = sorted({int(x) for x in v})
        if not v or any(x < 1 or x > 100 for x in v):
            raise ValueError("thresholds must be between 1 and 100")
        return v

    @field_validator("operation_credits")
    @classmethod
    def _credits(cls, v: dict[str, int]) -> dict[str, int]:
        unknown = set(v) - set(OPERATION_CATEGORIES)
        if unknown:
            raise ValueError(f"unknown operation categories: {sorted(unknown)}")
        if any(not 0 <= int(x) <= 20 for x in v.values()):
            raise ValueError("credits must be between 0 and 20")
        return {k: int(v.get(k, 1 if "question" in k else 0)) for k in OPERATION_CATEGORIES}


async def get_quota_policy() -> QuotaPolicy:
    doc = await get_db().system_settings.find_one({"_id": "quota_policy"})
    return QuotaPolicy.model_validate({k: v for k, v in (doc or {}).items() if k not in ("_id", "updated_at", "updated_by")})


async def save_quota_policy(policy: QuotaPolicy, actor_id: Any) -> QuotaPolicy:
    await get_db().system_settings.update_one(
        {"_id": "quota_policy"}, {"$set": {**policy.model_dump(), "updated_at": utcnow(), "updated_by": actor_id}}, upsert=True
    )
    return policy


async def question_credits(category: str) -> int:
    policy = await get_quota_policy()
    if policy.question_credit_mode == "count":
        return 1 if category.endswith("question") else 0
    return max(0, policy.operation_credits.get(category, 1))


# --- Runtime settings -----------------------------------------------------------------------

RUNTIME_CACHE_SECONDS = 5.0


class RuntimeSettings(BaseModel):
    """Settings administrators change in Admin > Settings, without a restart.

    Environment variables supply the defaults; a saved value overrides its default. The API
    and every worker pick a change up within ``RUNTIME_CACHE_SECONDS``.
    """

    model_config = ConfigDict(extra="forbid")

    # Where an upload waits for the worker: the server's disk, or MongoDB (GridFS).
    storage_backend: Literal["local", "mongodb", "s3"]
    # Off: the original is deleted once its text and vectors are indexed.
    keep_original_files: bool
    registration_enabled: bool
    context_max_tokens: int = Field(ge=1000, le=200_000)
    retrieval_top_k: int = Field(ge=1, le=50)
    retrieval_candidates: int = Field(ge=5, le=400)
    graph_extraction_enabled: bool
    platform_monthly_ai_budget_usd: float = Field(ge=0, le=1_000_000)

    @model_validator(mode="after")
    def _consistent(self) -> RuntimeSettings:
        if self.retrieval_candidates < self.retrieval_top_k:
            raise ValueError("retrieval_candidates must be at least retrieval_top_k")
        return self


def runtime_defaults() -> RuntimeSettings:
    s = get_settings()
    return RuntimeSettings(
        storage_backend=s.storage_backend,
        keep_original_files=s.keep_original_files,
        registration_enabled=s.registration_enabled,
        context_max_tokens=s.context_max_tokens,
        retrieval_top_k=s.retrieval_top_k,
        retrieval_candidates=s.retrieval_candidates,
        graph_extraction_enabled=s.feature_graph_extraction,
        platform_monthly_ai_budget_usd=s.platform_monthly_ai_budget_usd,
    )


_runtime_cache: tuple[str, float, RuntimeSettings] | None = None


def reset_runtime_settings_cache() -> None:
    global _runtime_cache
    _runtime_cache = None


async def get_runtime_settings() -> RuntimeSettings:
    global _runtime_cache
    db = get_db()
    now = time.monotonic()
    if _runtime_cache and _runtime_cache[0] == db.name and _runtime_cache[1] > now:
        return _runtime_cache[2]
    defaults = runtime_defaults()
    doc = await db.system_settings.find_one({"_id": "runtime"})
    saved = {k: v for k, v in (doc or {}).items() if k in RuntimeSettings.model_fields}
    try:
        value = RuntimeSettings.model_validate({**defaults.model_dump(), **saved})
    except ValidationError:
        # A saved value no longer fits (for example after a downgrade): run on the defaults.
        log.error("runtime_settings_invalid", extra={"fields": sorted(saved)})
        value = defaults
    _runtime_cache = (db.name, now + RUNTIME_CACHE_SECONDS, value)
    return value


async def runtime_settings_meta() -> dict[str, Any]:
    doc = await get_db().system_settings.find_one({"_id": "runtime"}, projection={"updated_at": 1, "updated_by": 1}) or {}
    return {"updated_at": doc.get("updated_at"), "updated_by": doc.get("updated_by")}


async def save_runtime_settings(value: RuntimeSettings, actor_id: Any) -> RuntimeSettings:
    await get_db().system_settings.update_one(
        {"_id": "runtime"}, {"$set": {**value.model_dump(), "updated_at": utcnow(), "updated_by": actor_id}}, upsert=True
    )
    reset_runtime_settings_cache()
    return value
