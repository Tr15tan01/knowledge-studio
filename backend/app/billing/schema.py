"""Validated plan, pricing and entitlement schemas.

Only the fields defined here can be configured. Administrators cannot insert
arbitrary keys or executable configuration (``extra="forbid"`` everywhere).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.units import GIB, MIB

FEATURE_KEYS: tuple[str, ...] = (
    "semantic_search",
    "hybrid_search",
    "basic_knowledge_graph",
    "advanced_knowledge_graph",
    "deep_research",
    "cross_document_research",
    "pdf_export",
    "markdown_export",
    "advanced_filters",
    "saved_searches",
    "priority_processing",
)

FEATURE_LABELS: dict[str, str] = {
    "semantic_search": "Semantic search",
    "hybrid_search": "Hybrid keyword + semantic search",
    "basic_knowledge_graph": "Knowledge graph",
    "advanced_knowledge_graph": "Advanced graph filters",
    "deep_research": "Deep research reports",
    "cross_document_research": "Cross-document research",
    "pdf_export": "PDF export",
    "markdown_export": "Markdown export",
    "advanced_filters": "Advanced library filters",
    "saved_searches": "Saved searches",
    "priority_processing": "Higher processing priority (fair use)",
}

# Hard ceilings protect the platform from configuration mistakes.
MAX_STORAGE_BYTES = 10 * 1024 * GIB  # 10 TiB
MAX_FILE_BYTES = 2 * GIB


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Entitlements(Strict):
    upload_limit_per_period: int = Field(ge=0, le=1_000_000)
    storage_limit_bytes: int = Field(ge=0, le=MAX_STORAGE_BYTES)
    max_file_size_bytes: int = Field(ge=1, le=MAX_FILE_BYTES)
    question_limit_per_period: int = Field(ge=0, le=10_000_000)
    research_report_limit_per_period: int = Field(ge=0, le=1_000_000)
    max_concurrent_research_jobs: int = Field(ge=0, le=100)
    max_saved_research_projects: int = Field(ge=0, le=100_000)
    max_concurrent_jobs: int = Field(default=3, ge=1, le=200)
    max_input_tokens_per_operation: int = Field(ge=256, le=2_000_000)
    max_output_tokens_per_operation: int = Field(ge=64, le=200_000)
    max_research_steps: int = Field(ge=1, le=200)
    max_retrieval_iterations: int = Field(default=6, ge=1, le=200)
    max_ai_calls_per_operation: int = Field(ge=1, le=500)
    max_operation_seconds: int = Field(default=600, ge=10, le=7200)
    daily_compute_budget: int = Field(ge=0, le=100_000_000, description="Compute units per UTC day (1 unit = 1,000 weighted tokens).")
    monthly_compute_budget: int = Field(ge=0, le=1_000_000_000)
    max_daily_cost_usd: float = Field(default=1.0, ge=0, le=100_000)
    max_monthly_cost_usd: float = Field(default=5.0, ge=0, le=1_000_000)
    max_pdf_export_pages: int = Field(ge=0, le=10_000)
    features: dict[str, bool]

    @field_validator("features")
    @classmethod
    def _known_features(cls, v: dict[str, bool]) -> dict[str, bool]:
        unknown = set(v) - set(FEATURE_KEYS)
        if unknown:
            raise ValueError(f"Unsupported feature flags: {', '.join(sorted(unknown))}")
        return {k: bool(v.get(k, False)) for k in FEATURE_KEYS}

    @model_validator(mode="after")
    def _consistent(self) -> Entitlements:
        if self.max_file_size_bytes > self.storage_limit_bytes and self.storage_limit_bytes > 0:
            raise ValueError("max_file_size_bytes cannot exceed storage_limit_bytes")
        if self.daily_compute_budget > self.monthly_compute_budget:
            raise ValueError("daily_compute_budget cannot exceed monthly_compute_budget")
        return self


class EntitlementOverrides(Strict):
    """Partial entitlements for promotional grants. Every field optional."""

    upload_limit_per_period: int | None = Field(default=None, ge=0)
    storage_limit_bytes: int | None = Field(default=None, ge=0, le=MAX_STORAGE_BYTES)
    max_file_size_bytes: int | None = Field(default=None, ge=1, le=MAX_FILE_BYTES)
    question_limit_per_period: int | None = Field(default=None, ge=0)
    research_report_limit_per_period: int | None = Field(default=None, ge=0)
    max_concurrent_research_jobs: int | None = Field(default=None, ge=0, le=100)
    max_saved_research_projects: int | None = Field(default=None, ge=0)
    daily_compute_budget: int | None = Field(default=None, ge=0)
    monthly_compute_budget: int | None = Field(default=None, ge=0)
    features: dict[str, bool] | None = None

    @field_validator("features")
    @classmethod
    def _known(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v is None:
            return None
        unknown = set(v) - set(FEATURE_KEYS)
        if unknown:
            raise ValueError(f"Unsupported feature flags: {', '.join(sorted(unknown))}")
        return v


ADJUSTABLE_METRICS = ("uploads", "questions", "research_reports")


class QuotaAdjustment(Strict):
    """A one-off additive change to the current period's allowance."""

    metric: Literal["uploads", "questions", "research_reports"]
    delta: int = Field(ge=-100_000, le=100_000)


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class Pricing(Strict):
    currency: str = "USD"
    monthly_display_price_minor: int | None = Field(default=None, ge=0, le=10_000_000, description="Minor units, e.g. 799 = $7.99")
    annual_display_price_minor: int | None = Field(default=None, ge=0, le=100_000_000)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        v = v.upper()
        if not CURRENCY_RE.match(v):
            raise ValueError("currency must be a 3-letter ISO code")
        return v


class Marketing(Strict):
    tagline: str = Field(default="", max_length=160)
    highlights: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("highlights")
    @classmethod
    def _hl(cls, v: list[str]) -> list[str]:
        out = [h.strip()[:140] for h in v if h.strip()]
        return out


class PlanCreate(Strict):
    name: str = Field(min_length=2, max_length=60)
    slug: str
    description: str = Field(default="", max_length=600)
    is_public: bool = False
    display_order: int = Field(default=100, ge=0, le=10_000)
    pricing: Pricing
    marketing: Marketing = Field(default_factory=Marketing)
    entitlements: Entitlements

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("slug must be lowercase letters, digits and hyphens (2-41 chars)")
        return v


ApplyPolicy = Literal["new_subscribers_only", "all_subscribers", "cohort"]


class PlanUpdate(Strict):
    """Editing a plan always creates a new immutable version."""

    name: str | None = Field(default=None, min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=600)
    is_public: bool | None = None
    is_active: bool | None = None
    display_order: int | None = Field(default=None, ge=0, le=10_000)
    pricing: Pricing | None = None
    marketing: Marketing | None = None
    entitlements: Entitlements | None = None
    change_note: str = Field(min_length=3, max_length=500)
    apply_policy: ApplyPolicy = "new_subscribers_only"
    cohort_account_ids: list[str] = Field(default_factory=list, max_length=10_000)
    confirm: bool = False


def default_free_entitlements() -> Entitlements:
    return Entitlements(
        upload_limit_per_period=5,
        storage_limit_bytes=500 * MIB,
        max_file_size_bytes=25 * MIB,
        question_limit_per_period=30,
        research_report_limit_per_period=1,
        max_concurrent_research_jobs=1,
        max_saved_research_projects=1,
        max_concurrent_jobs=2,
        max_input_tokens_per_operation=24_000,
        max_output_tokens_per_operation=2_048,
        max_research_steps=8,
        max_retrieval_iterations=6,
        max_ai_calls_per_operation=16,
        max_operation_seconds=600,
        daily_compute_budget=400,
        monthly_compute_budget=3_000,
        max_daily_cost_usd=0.5,
        max_monthly_cost_usd=2.0,
        max_pdf_export_pages=0,
        features={
            "semantic_search": True,
            "hybrid_search": True,
            "basic_knowledge_graph": True,
            "deep_research": True,  # bounded by research_report_limit_per_period = 1
            "markdown_export": True,
        },
    )


def default_premium_entitlements() -> Entitlements:
    return Entitlements(
        upload_limit_per_period=20,
        storage_limit_bytes=5 * GIB,
        max_file_size_bytes=100 * MIB,
        question_limit_per_period=100,
        research_report_limit_per_period=10,
        max_concurrent_research_jobs=2,
        max_saved_research_projects=5,
        max_concurrent_jobs=4,
        max_input_tokens_per_operation=64_000,
        max_output_tokens_per_operation=6_000,
        max_research_steps=20,
        max_retrieval_iterations=12,
        max_ai_calls_per_operation=40,
        max_operation_seconds=1_200,
        daily_compute_budget=3_000,
        monthly_compute_budget=30_000,
        max_daily_cost_usd=3.0,
        max_monthly_cost_usd=20.0,
        max_pdf_export_pages=300,
        features={k: True for k in FEATURE_KEYS},
    )


def seed_plans() -> list[PlanCreate]:
    return [
        PlanCreate(
            name="Free",
            slug="free",
            description="Build a personal library and ask questions grounded in your own sources.",
            is_public=True,
            display_order=10,
            pricing=Pricing(currency="USD", monthly_display_price_minor=0, annual_display_price_minor=0),
            marketing=Marketing(
                tagline="Start your knowledge library",
                highlights=[
                    "Personal library and document organization",
                    "Semantic and keyword search",
                    "Source-cited questions and answers",
                    "Basic knowledge graph",
                    "Markdown export",
                ],
            ),
            entitlements=default_free_entitlements(),
        ),
        PlanCreate(
            name="Premium",
            slug="premium",
            description="For serious readers and researchers working across many books.",
            is_public=True,
            display_order=20,
            pricing=Pricing(currency="USD", monthly_display_price_minor=799, annual_display_price_minor=7900),
            marketing=Marketing(
                tagline="Research across your whole library",
                highlights=[
                    "Cross-document deep research",
                    "Advanced graph filters",
                    "Markdown and PDF export",
                    "Advanced filters and saved searches",
                    "Higher processing priority, subject to fair use",
                ],
            ),
            entitlements=default_premium_entitlements(),
        ),
    ]
