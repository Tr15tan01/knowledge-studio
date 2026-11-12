"""Question answering orchestration (streamed as events).

Quota policy for questions (docs/billing-and-plans.md):
* Question credits, compute and spending budgets are reserved atomically *before* any
  provider call.
* If retrieval finds no evidence, no model call is made and the question is not charged.
* If the provider fails before producing output, everything is released (compute actually
  consumed is still recorded internally).
* Once the model has produced an answer the question is charged, even if the client
  disconnects mid-stream.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from bson import ObjectId

from app.auth.deps import WorkspaceContext
from app.billing.entitlements import require_feature, resolve
from app.core.config import get_settings
from app.core.errors import AppError, NotFound, ProviderError
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.database.ids import oid
from app.providers.llm import CallContext, ChatMessage, get_llm
from app.providers.telemetry import estimate_cost_usd
from app.retrieval.budget import classify_question, estimate_question, reserve_operation
from app.retrieval.rag import build_sources_block, build_user_message, system_prompt, validate_citations
from app.retrieval.search import RetrievalFilters, expand_with_neighbours, hybrid_search, select_context
from app.security import ratelimit

log = logging.getLogger("app.ask")


async def get_or_create_conversation(
    ws: WorkspaceContext, conversation_id: str | None, question: str, filters: dict[str, Any]
) -> dict[str, Any]:
    db = get_db()
    if conversation_id:
        conv = await db.conversations.find_one(ws.scope(_id=oid(conversation_id, what="conversation"), user_id=ws.user_id))
        if not conv:
            raise NotFound("Conversation not found.")
        return conv
    now = utcnow()
    doc = {
        **ws.scope(),
        "user_id": ws.user_id,
        "title": question.strip()[:80],
        "filters": filters,
        "message_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    res = await db.conversations.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def _history(conv_id: ObjectId, limit: int = 6) -> list[tuple[str, str]]:
    rows = [
        m
        async for m in get_db()
        .messages.find({"conversation_id": conv_id, "status": "complete"}, projection={"role": 1, "content": 1})
        .sort("created_at", -1)
        .limit(limit)
    ]
    return [(m["role"], m["content"]) for m in reversed(rows)]


def retrieval_query(question: str, history: list[tuple[str, str]]) -> str:
    """Deterministic follow-up rewriting: short follow-ups are combined with the previous question."""
    prev_user = next((t for r, t in reversed(history) if r == "user"), None)
    if prev_user and len(question.split()) < 8:
        return f"{prev_user.strip()} {question.strip()}"
    return question


async def answer_question(
    ws: WorkspaceContext,
    *,
    question: str,
    conversation_id: str | None,
    filters: RetrievalFilters,
    filters_public: dict[str, Any],
    answer_length: str = "standard",
    idempotency_key: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    s = get_settings()
    db = get_db()
    await ratelimit.hit("ai", str(ws.user_id), limit=s.rate_limit_ai_per_minute, window_seconds=60)
    r = await resolve(ws.account_id)
    require_feature(r, "semantic_search", label="AI questions")
    conv = await get_or_create_conversation(ws, conversation_id, question, filters_public)
    yield {"type": "conversation", "id": str(conv["_id"]), "title": conv.get("title")}

    selected = len(filters.source_ids) + len(filters.file_ids)
    category = classify_question(question, selected_sources=selected, answer_length=answer_length)
    est = await estimate_question(r, category, answer_length=answer_length)
    yield {"type": "estimate", **est.public()}

    hold = await reserve_operation(r, est, idempotency_key=idempotency_key)
    now = utcnow()
    user_msg = {
        "conversation_id": conv["_id"],
        **ws.scope(),
        "user_id": ws.user_id,
        "role": "user",
        "content": question.strip(),
        "filters": filters_public,
        "status": "complete",
        "created_at": now,
    }
    await db.messages.insert_one(user_msg)
    assistant_id = ObjectId()
    charged = False
    try:
        history = await _history(conv["_id"])
        history = [h for h in history if h[1] != question.strip()][-6:]
        rq = retrieval_query(question, history)
        retrieval = await hybrid_search(ws.workspace_id, rq, filters=filters, account_id=ws.account_id)
        budget = min(est.input_tokens - 1500, r.entitlements.max_input_tokens_per_operation - 1500)
        extras = await expand_with_neighbours(ws.workspace_id, retrieval.hits[:6])
        chosen = select_context(retrieval.hits, budget, extras)
        sources_public = [h.to_public() for h, _ in chosen]
        yield {"type": "sources", "items": sources_public, "degraded": retrieval.degraded, "counts": retrieval.counts}

        if not chosen:
            await hold.release(reason="no_evidence")
            text = "I couldn't find anything in your library that addresses this question. Try different wording, remove filters, or upload sources that cover this topic."
            msg = await _save_assistant(
                assistant_id,
                conv,
                ws,
                text,
                [],
                insufficient=True,
                retrieval=retrieval,
                category=category,
                charged=False,
                status="complete",
            )
            yield {"type": "done", "message": msg}
            return

        sources_block, mapping = build_sources_block(chosen)
        user_text = build_user_message(question, sources_block, history)
        llm = get_llm("answer")
        hold.use_call()
        parts: list[str] = []
        result = None
        async for chunk in llm.stream(
            system=system_prompt((ws.auth.user.get("preferences") or {}).get("answer_language")),
            messages=[ChatMessage("user", user_text)],
            max_output_tokens=est.max_output_tokens,
            ctx=CallContext(account_id=ws.account_id, workspace_id=ws.workspace_id, operation=category),
        ):
            if chunk.text:
                parts.append(chunk.text)
                charged = True
                yield {"type": "delta", "text": chunk.text}
            if chunk.done:
                result = chunk.result
        raw = "".join(parts)
        if result:
            hold.add_usage(
                result.input_tokens, result.output_tokens, await estimate_cost_usd(result.model, result.input_tokens, result.output_tokens)
            )
        if not raw.strip():
            raise ProviderError("The AI provider returned an empty answer. Please retry.", code="provider_empty", retryable=True)
        validated = validate_citations(raw, mapping)
        await hold.commit(charge_allowance=True)
        msg = await _save_assistant(
            assistant_id,
            conv,
            ws,
            validated.text,
            validated.citations,
            insufficient=validated.insufficient,
            retrieval=retrieval,
            category=category,
            charged=True,
            status="complete",
            invalid_markers=validated.invalid_markers,
            model=result.model if result else None,
        )
        yield {"type": "done", "message": msg}
    except AppError as exc:
        if charged:
            await hold.commit(charge_allowance=True)
        else:
            await hold.release(reason=exc.code)
        fallback = None
        if isinstance(exc, ProviderError) or exc.code.startswith("provider"):
            try:
                kw = await hybrid_search(ws.workspace_id, question, filters=filters, use_vector=False)
                fallback = {"label": "Keyword search results (no AI answer)", "items": [h.to_public() for h in kw.hits[:8]]}
            except AppError:
                fallback = None
        await _save_assistant(
            assistant_id,
            conv,
            ws,
            "",
            [],
            insufficient=False,
            retrieval=None,
            category=category,
            charged=charged,
            status="failed",
            error={"code": exc.code, "message": exc.message},
        )
        yield {"type": "error", "code": exc.code, "message": exc.message, "retryable": exc.retryable, "fallback": fallback}
    finally:
        # Client disconnects cancel the generator: settle holds deterministically.
        if not hold.settled:
            if charged:
                await hold.commit(charge_allowance=True)
            else:
                await hold.release(reason="interrupted")


async def _save_assistant(
    mid: ObjectId,
    conv: dict[str, Any],
    ws: WorkspaceContext,
    text: str,
    citations: list[dict[str, Any]],
    *,
    insufficient: bool,
    retrieval: Any,
    category: str,
    charged: bool,
    status: str,
    invalid_markers: list[str] | None = None,
    model: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db = get_db()
    now = utcnow()
    doc = {
        "_id": mid,
        "conversation_id": conv["_id"],
        **ws.scope(),
        "user_id": ws.user_id,
        "role": "model",
        "content": text,
        "citations": citations,
        "insufficient_evidence": insufficient,
        "status": status,
        "error": error,
        "retrieval": {"degraded": retrieval.degraded, "counts": retrieval.counts, "timings_ms": retrieval.timings_ms}
        if retrieval
        else None,
        "operation": {"category": category, "charged": charged, "model": model},
        "citation_validation": {"removed_markers": invalid_markers or []},
        "created_at": now,
    }
    await db.messages.insert_one(doc)
    await db.conversations.update_one({"_id": conv["_id"]}, {"$set": {"updated_at": now}, "$inc": {"message_count": 2}})
    return message_out(doc)


def message_out(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(m["_id"]),
        "conversation_id": str(m["conversation_id"]),
        "role": "assistant" if m["role"] == "model" else "user",
        "content": m.get("content", ""),
        "citations": m.get("citations", []),
        "insufficient_evidence": m.get("insufficient_evidence", False),
        "status": m.get("status"),
        "error": m.get("error"),
        "retrieval": m.get("retrieval"),
        "operation": m.get("operation"),
        "created_at": m["created_at"].isoformat(),
    }
