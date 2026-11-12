"""Ask AI (streamed), conversations, library search and saved searches."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.auth.deps import Workspace
from app.billing.entitlements import require_feature, resolve
from app.core.errors import NotFound
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.database.ids import maybe_oid, oid
from app.retrieval.ask import answer_question, message_out
from app.retrieval.budget import classify_question, estimate_question
from app.retrieval.search import RetrievalFilters, hybrid_search
from app.schemas.common import APIModel, iso, paging
from app.security import ratelimit

router = APIRouter(tags=["ask"])


class FiltersIn(APIModel):
    source_ids: list[str] = Field(default_factory=list, max_length=50)
    file_ids: list[str] = Field(default_factory=list, max_length=50)
    collection_id: str | None = None
    source_type: Literal["book", "document"] | None = None
    language: str | None = Field(default=None, max_length=10)
    author: str | None = Field(default=None, max_length=120)
    date_from: str | None = Field(default=None, max_length=20)
    date_to: str | None = Field(default=None, max_length=20)

    def to_filters(self) -> RetrievalFilters:
        return RetrievalFilters(
            source_ids=[i for i in (maybe_oid(x) for x in self.source_ids) if i],
            file_ids=[i for i in (maybe_oid(x) for x in self.file_ids) if i],
            collection_id=maybe_oid(self.collection_id),
            source_type=self.source_type,
            language=self.language,
            author=self.author,
            date_from=self.date_from,
            date_to=self.date_to,
        )

    def uses_advanced(self) -> bool:
        return bool(self.author or self.date_from or self.date_to or self.language)


class AskIn(APIModel):
    question: str = Field(min_length=2, max_length=4000)
    conversation_id: str | None = None
    filters: FiltersIn = Field(default_factory=FiltersIn)
    answer_length: Literal["short", "standard", "long"] = "standard"


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


async def _check_filters(ws: Any, f: FiltersIn) -> None:
    if f.uses_advanced():
        require_feature(await resolve(ws.account_id), "advanced_filters", label="Advanced filters")


@router.post("/ask/estimate")
async def estimate(body: AskIn, ws: Workspace) -> dict[str, Any]:
    r = await resolve(ws.account_id)
    cat = classify_question(
        body.question, selected_sources=len(body.filters.source_ids) + len(body.filters.file_ids), answer_length=body.answer_length
    )
    est = await estimate_question(r, cat, answer_length=body.answer_length)
    return est.public()


@router.post("/ask")
async def ask(
    body: AskIn,
    ws: Workspace,
    request: Request,
    accept: str = Header("application/json"),
    idempotency_key: str | None = Header(None, max_length=100),
) -> Any:
    await _check_filters(ws, body.filters)
    gen = answer_question(
        ws,
        question=body.question,
        conversation_id=body.conversation_id,
        filters=body.filters.to_filters(),
        filters_public=body.filters.model_dump(),
        answer_length=body.answer_length,
        idempotency_key=idempotency_key,
    )
    if "text/event-stream" in accept:
        # Run the first step eagerly so quota/auth errors return a normal JSON error.
        first = await gen.__anext__()

        async def stream() -> AsyncIterator[str]:
            yield _sse(first)
            async for ev in gen:
                if await request.is_disconnected():
                    await gen.aclose()
                    break
                yield _sse(ev)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    events = [ev async for ev in gen]
    out: dict[str, Any] = {"events": [e["type"] for e in events]}
    for ev in events:
        if ev["type"] == "conversation":
            out["conversation_id"] = ev["id"]
        elif ev["type"] == "estimate":
            out["estimate"] = {k: v for k, v in ev.items() if k != "type"}
        elif ev["type"] == "sources":
            out["sources"] = ev["items"]
            out["degraded"] = ev["degraded"]
        elif ev["type"] == "done":
            out["message"] = ev["message"]
        elif ev["type"] == "error":
            out["error"] = {k: v for k, v in ev.items() if k != "type"}
    return out


@router.get("/conversations")
async def list_conversations(ws: Workspace, page: int = 1, page_size: int = 30) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt = ws.scope(user_id=ws.user_id)
    db = get_db()
    total = await db.conversations.count_documents(flt)
    items = [
        {"id": str(c["_id"]), "title": c.get("title"), "message_count": c.get("message_count", 0), "updated_at": iso(c.get("updated_at"))}
        async for c in db.conversations.find(flt).sort("updated_at", -1).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _conv(ws: Any, cid: str) -> dict[str, Any]:
    c = await get_db().conversations.find_one(ws.scope(_id=oid(cid, what="conversation"), user_id=ws.user_id))
    if not c:
        raise NotFound("Conversation not found.")
    return c


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, ws: Workspace) -> dict[str, Any]:
    c = await _conv(ws, conversation_id)
    msgs = [
        message_out(m) async for m in get_db().messages.find({"conversation_id": c["_id"], **ws.scope()}).sort("created_at", 1).limit(500)
    ]
    return {"id": str(c["_id"]), "title": c.get("title"), "filters": c.get("filters"), "messages": msgs}


class RenameIn(APIModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str, body: RenameIn, ws: Workspace) -> dict[str, str]:
    c = await _conv(ws, conversation_id)
    await get_db().conversations.update_one({"_id": c["_id"]}, {"$set": {"title": body.title}})
    return {"status": "updated"}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, ws: Workspace) -> dict[str, str]:
    c = await _conv(ws, conversation_id)
    await get_db().messages.delete_many({"conversation_id": c["_id"]})
    await get_db().conversations.delete_one({"_id": c["_id"]})
    return {"status": "deleted"}


@router.get("/conversations/{conversation_id}/export.md")
async def export_conversation(conversation_id: str, ws: Workspace) -> StreamingResponse:
    require_feature(await resolve(ws.account_id), "markdown_export", label="Markdown export")
    c = await _conv(ws, conversation_id)
    lines = [f"# {c.get('title') or 'Conversation'}", ""]
    async for m in get_db().messages.find({"conversation_id": c["_id"], "status": "complete"}).sort("created_at", 1):
        who = "**You**" if m["role"] == "user" else "**Knowledge Studio**"
        lines += [who, "", m.get("content", ""), ""]
        for cit in m.get("citations", []):
            lines.append(f"- [{cit['marker']}] {cit['source_title']} — {cit.get('citation_label') or ''}")
        lines.append("")
    body = "\n".join(lines)
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", c.get("title") or "conversation")[:60] or "conversation"

    async def gen() -> AsyncIterator[bytes]:
        yield body.encode()

    return StreamingResponse(
        gen(), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe}.md"'}
    )


# --- Search without AI generation -----------------------------------------------------------


class SearchIn(APIModel):
    query: str = Field(min_length=1, max_length=500)
    filters: FiltersIn = Field(default_factory=FiltersIn)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/search")
async def search(body: SearchIn, ws: Workspace) -> dict[str, Any]:
    await ratelimit.hit("search", str(ws.user_id), limit=60, window_seconds=60)
    r = await resolve(ws.account_id)
    require_feature(r, "semantic_search" if body.mode != "keyword" else "hybrid_search", label="Search")
    await _check_filters(ws, body.filters)
    res = await hybrid_search(
        ws.workspace_id,
        body.query,
        filters=body.filters.to_filters(),
        top_k=body.limit,
        use_vector=body.mode != "keyword",
        account_id=ws.account_id,
    )
    hits = res.hits
    if body.mode == "semantic":
        hits = [h for h in hits if "vector" in h.scores]
    return {
        "items": [h.to_public() for h in hits[: body.limit]],
        "degraded": res.degraded,
        "counts": res.counts,
        "timings_ms": res.timings_ms,
    }


class SavedSearchIn(APIModel):
    name: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=1, max_length=500)
    filters: FiltersIn = Field(default_factory=FiltersIn)


@router.get("/saved-searches")
async def list_saved(ws: Workspace) -> dict[str, Any]:
    items = [
        {"id": str(x["_id"]), "name": x["name"], "query": x["query"], "filters": x.get("filters", {}), "created_at": iso(x["created_at"])}
        async for x in get_db().saved_searches.find(ws.scope(user_id=ws.user_id)).sort("created_at", -1).limit(200)
    ]
    return {"items": items}


@router.post("/saved-searches", status_code=201)
async def create_saved(body: SavedSearchIn, ws: Workspace) -> dict[str, Any]:
    require_feature(await resolve(ws.account_id), "saved_searches", label="Saved searches")
    res = await get_db().saved_searches.insert_one(
        {
            **ws.scope(),
            "user_id": ws.user_id,
            "name": body.name,
            "query": body.query,
            "filters": body.filters.model_dump(),
            "created_at": utcnow(),
        }
    )
    return {"id": str(res.inserted_id)}


@router.delete("/saved-searches/{saved_id}")
async def delete_saved(saved_id: str, ws: Workspace) -> dict[str, str]:
    res = await get_db().saved_searches.delete_one(ws.scope(_id=oid(saved_id), user_id=ws.user_id))
    if not res.deleted_count:
        raise NotFound("Saved search not found.")
    return {"status": "deleted"}
