"""Operation classification, pre-execution estimates and bounded compute budgets.

* Classification is deterministic (operation type, number of selected sources, question
  shape, requested length). No model output can change a category, quota or limit.
* Before execution we estimate model calls, tokens, retrieval operations and research steps.
* A ``BudgetHold`` atomically reserves the user-facing allowance (question credits or a
  research report) plus internal compute and spending budgets (daily/monthly per account and
  a platform-wide monthly ceiling). Actual usage is committed afterwards; holds are released
  on failure. Hard per-operation limits (calls, tokens, steps, duration) are enforced by
  ``BudgetHold.use_call`` and ``remaining_seconds``.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.billing import usage
from app.billing.entitlements import Resolved, reserve_metric
from app.billing.periods import calendar_month
from app.core.config import get_settings
from app.core.errors import AppError, QuotaExceeded
from app.core.timeutil import utcnow
from app.providers.telemetry import compute_units, estimate_cost_usd
from app.services.system_settings import get_runtime_settings, question_credits

COMPARE_RE = re.compile(r"\b(compare|comparison|contrast|differ|difference|versus|vs\.?|across|between .+ and|შეადარე|განსხვავება)\b", re.I)
SIMPLE_MAX_WORDS = 12


class BudgetExhausted(AppError):
    status_code = 429
    code = "operation_budget_exhausted"


@dataclass
class OperationEstimate:
    category: str
    model_calls: int
    input_tokens: int
    max_output_tokens: int
    retrieval_operations: int
    research_steps: int
    compute_units: int
    cost_usd: float
    credits: int
    expensive: bool
    explanation: str | None

    def public(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "model_calls": self.model_calls,
            "estimated_input_tokens": self.input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "retrieval_operations": self.retrieval_operations,
            "research_steps": self.research_steps,
            "question_credits": self.credits,
            "expensive": self.expensive,
            "explanation": self.explanation,
            "is_estimate": True,
        }


def classify_question(question: str, *, selected_sources: int, answer_length: str = "standard") -> str:
    words = len(question.split())
    if selected_sources >= 2 or COMPARE_RE.search(question):
        return "cross_document_question"
    if words <= SIMPLE_MAX_WORDS and answer_length == "short":
        return "simple_question"
    if words <= SIMPLE_MAX_WORDS and selected_sources == 1:
        return "simple_question"
    return "standard_question"


def output_tokens_for(answer_length: str, cap: int) -> int:
    return min(cap, {"short": 500, "standard": 1200, "long": 2500}.get(answer_length, 1200))


async def estimate_question(r: Resolved, category: str, *, answer_length: str = "standard") -> OperationEstimate:
    e = r.entitlements
    s = get_settings()
    context_max = (await get_runtime_settings()).context_max_tokens
    context = min(context_max, e.max_input_tokens_per_operation - 1500)
    if category == "simple_question":
        context = min(context, 6000)
    if category == "cross_document_question":
        context = min(e.max_input_tokens_per_operation - 1500, int(context_max * 1.5))
    in_tok = context + 1500
    out_tok = output_tokens_for(answer_length, e.max_output_tokens_per_operation)
    credits = await question_credits(category)
    cost = await estimate_cost_usd(s.llm_model, in_tok, out_tok)
    expensive = category == "cross_document_question"
    return OperationEstimate(
        category=category,
        model_calls=1,
        input_tokens=in_tok,
        max_output_tokens=out_tok,
        retrieval_operations=2,
        research_steps=0,
        compute_units=compute_units(in_tok, out_tok),
        cost_usd=cost,
        credits=credits,
        expensive=expensive,
        explanation="This cross-book comparison may use more AI resources than a standard question." if expensive else None,
    )


async def estimate_research(r: Resolved, *, max_steps: int | None = None) -> OperationEstimate:
    e = r.entitlements
    s = get_settings()
    steps = min(max_steps or e.max_research_steps, e.max_research_steps)
    calls = min(e.max_ai_calls_per_operation, steps + 4)
    per_call_in = min(e.max_input_tokens_per_operation, 9000)
    in_tok = calls * per_call_in
    out_tok = calls * min(1500, e.max_output_tokens_per_operation)
    cost = await estimate_cost_usd(s.research_llm_model, in_tok, out_tok)
    return OperationEstimate(
        category="deep_research",
        model_calls=calls,
        input_tokens=in_tok,
        max_output_tokens=e.max_output_tokens_per_operation,
        retrieval_operations=min(e.max_retrieval_iterations, steps) * 2,
        research_steps=steps,
        compute_units=compute_units(in_tok, out_tok),
        cost_usd=cost,
        credits=0,
        expensive=True,
        explanation="A deep-research report runs several retrieval and synthesis steps and uses your separate report allowance.",
    )


@dataclass
class BudgetHold:
    resolved: Resolved
    estimate: OperationEstimate
    reservations: dict[str, usage.Reservation] = field(default_factory=dict)
    calls_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    started: float = field(default_factory=time.monotonic)
    settled: bool = False
    max_seconds: int = 600

    def use_call(self) -> None:
        """Count a model call against the operation's hard limit (raises when exhausted)."""
        if self.calls_used >= self.resolved.entitlements.max_ai_calls_per_operation:
            raise BudgetExhausted("This operation reached its maximum number of AI calls.")
        self.calls_used += 1

    def add_usage(self, input_tokens: int, output_tokens: int, cost_usd: float = 0.0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost_usd

    @property
    def remaining_seconds(self) -> float:
        return self.max_seconds - (time.monotonic() - self.started)

    def compute_exhausted(self) -> bool:
        return compute_units(self.input_tokens, self.output_tokens) >= self.estimate.compute_units * 1.0

    async def commit(self, *, charge_allowance: bool = True) -> None:
        if self.settled:
            return
        self.settled = True
        units = compute_units(self.input_tokens, self.output_tokens)
        micro = int(round(self.cost_usd * 1_000_000))
        for metric, res in self.reservations.items():
            if metric in ("questions", "research_reports"):
                if charge_allowance:
                    await usage.commit(res.id)
                else:
                    await usage.release(res.id, reason="not_charged")
            elif metric in ("compute_daily", "compute_monthly"):
                await usage.commit(res.id, units)
            elif metric in ("cost_daily_micro_usd", "cost_monthly_micro_usd", "platform_cost_micro_usd"):
                await usage.commit(res.id, micro)
            else:
                await usage.release(res.id, reason="unused")

    async def release(self, reason: str = "failed") -> None:
        """Release all holds. If the provider already consumed tokens, record compute/cost actuals."""
        if self.settled:
            return
        self.settled = True
        units = compute_units(self.input_tokens, self.output_tokens)
        micro = int(round(self.cost_usd * 1_000_000))
        for metric, res in self.reservations.items():
            if metric in ("compute_daily", "compute_monthly") and units:
                await usage.commit(res.id, units)
            elif metric.endswith("micro_usd") and micro:
                await usage.commit(res.id, micro)
            else:
                await usage.release(res.id, reason=reason)


async def reserve_operation(
    r: Resolved, est: OperationEstimate, *, idempotency_key: str | None = None, ttl_seconds: int = 900
) -> BudgetHold:
    key = idempotency_key or uuid.uuid4().hex
    hold = BudgetHold(resolved=r, estimate=est, max_seconds=r.entitlements.max_operation_seconds)
    micro = int(round(est.cost_usd * 1_000_000))
    plan: list[tuple[str, int]] = []
    if est.category == "deep_research":
        plan.append(("research_reports", 1))
    elif est.credits:
        plan.append(("questions", est.credits))
    plan += [
        ("compute_daily", est.compute_units),
        ("compute_monthly", est.compute_units),
        ("cost_daily_micro_usd", micro),
        ("cost_monthly_micro_usd", micro),
    ]
    try:
        for metric, amount in plan:
            hold.reservations[metric] = await reserve_metric(
                r, metric, amount, idempotency_key=f"{metric}:{key}", ttl_seconds=ttl_seconds, meta={"category": est.category}
            )
        platform_limit = int((await get_runtime_settings()).platform_monthly_ai_budget_usd * 1_000_000)
        period = calendar_month(utcnow())
        hold.reservations["platform_cost_micro_usd"] = await usage.reserve(
            account_id=usage.PLATFORM_ACCOUNT,
            metric="platform_cost_micro_usd",
            amount=micro,
            limit=platform_limit,
            period=period,
            idempotency_key=f"platform:{key}",
            ttl=timedelta(seconds=ttl_seconds),
            meta={"account_id": str(r.account_id)},
            reset_at=period.end,
        )
    except QuotaExceeded:
        hold.settled = False
        await hold.release(reason="quota_denied")
        raise
    return hold
