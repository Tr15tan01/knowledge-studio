"""Knowledge graph API: bounded, server-side paginated exploration with provenance."""

from __future__ import annotations

import re
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Query

from app.auth.deps import Workspace
from app.billing.entitlements import require_feature, resolve
from app.core.errors import NotFound
from app.database.client import get_db
from app.database.ids import maybe_oid, oid
from app.graph.extraction import ENTITY_TYPES, REL_TYPES
from app.schemas.common import paging

router = APIRouter(prefix="/graph", tags=["graph"])

MAX_NODES = 150
MAX_EDGES = 400


def entity_out(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(e["_id"]),
        "name": e["name"],
        "type": e["type"],
        "description": e.get("description", ""),
        "mention_count": e.get("mention_count", 0),
        "source_count": len(e.get("source_ids", [])),
        "provenance": e.get("provenance", "ai_extracted"),
    }


def edge_out(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r["_id"]),
        "source": str(r["source_entity_id"]),
        "target": str(r["target_entity_id"]),
        "type": r["type"],
        "confidence": r.get("confidence"),
        "confidence_is_estimate": True,
        "evidence_count": r.get("evidence_count", len(r.get("evidence", []))),
        "provenance": r.get("provenance", "ai_extracted"),
        "verified": bool(r.get("verified", False)),
    }


async def _gate(ws: Any, *, advanced: bool) -> None:
    r = await resolve(ws.account_id)
    require_feature(r, "basic_knowledge_graph", label="Knowledge graph")
    if advanced:
        require_feature(r, "advanced_knowledge_graph", label="Advanced graph filters")


@router.get("/meta")
async def meta(ws: Workspace) -> dict[str, Any]:
    await _gate(ws, advanced=False)
    db = get_db()
    by_type = {
        row["_id"]: row["n"]
        async for row in await db.entities.aggregate([{"$match": ws.scope()}, {"$group": {"_id": "$type", "n": {"$sum": 1}}}])
    }
    files = {
        row["_id"] or "none": row["n"]
        async for row in await db.files.aggregate(
            [{"$match": ws.scope(storage_status="stored")}, {"$group": {"_id": "$graph_status", "n": {"$sum": 1}}}]
        )
    }
    return {
        "entity_types": list(ENTITY_TYPES),
        "relationship_types": list(REL_TYPES),
        "entities_by_type": by_type,
        "relationships": await db.relationships.count_documents(ws.scope()),
        "files_by_graph_status": files,
        "disclaimer": "Relationships are extracted by AI from your sources. They are not verified facts; open the evidence to check them.",
    }


@router.get("/entities")
async def list_entities(
    ws: Workspace,
    q: str | None = Query(None, max_length=120),
    type: str | None = None,
    source_id: str | None = None,
    page: int = 1,
    page_size: int = 30,
    sort: Literal["mentions", "name"] = "mentions",
) -> dict[str, Any]:
    await _gate(ws, advanced=bool(source_id))
    page, page_size, skip = paging(page, page_size, 100)
    flt: dict[str, Any] = ws.scope()
    if q:
        flt["$or"] = [{"name": {"$regex": re.escape(q), "$options": "i"}}, {"normalized_name": {"$regex": re.escape(q.lower())}}]
    if type in ENTITY_TYPES:
        flt["type"] = type
    if source_id and (s := maybe_oid(source_id)):
        flt["source_ids"] = s
    db = get_db()
    total = await db.entities.count_documents(flt)
    order = [("mention_count", -1), ("_id", 1)] if sort == "mentions" else [("name", 1)]
    items = [entity_out(e) async for e in db.entities.find(flt).sort(order).skip(skip).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _evidence(ws: Any, chunk_ids: list[ObjectId], limit: int = 20) -> list[dict[str, Any]]:
    out = []
    async for c in get_db().chunks.find(
        ws.scope(_id={"$in": chunk_ids[:limit]}),
        projection={
            "text": 1,
            "source_title": 1,
            "source_id": 1,
            "file_id": 1,
            "citation_label": 1,
            "section_path": 1,
            "page_start": 1,
            "overlap_prefix_chars": 1,
        },
    ):
        out.append(
            {
                "chunk_id": str(c["_id"]),
                "source_id": str(c["source_id"]),
                "file_id": str(c["file_id"]),
                "source_title": c.get("source_title"),
                "citation_label": c.get("citation_label"),
                "section_path": c.get("section_path", []),
                "page_start": c.get("page_start"),
                "snippet": c["text"][int(c.get("overlap_prefix_chars", 0)) :][:400],
            }
        )
    return out


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str, ws: Workspace, mentions_page: int = 1) -> dict[str, Any]:
    await _gate(ws, advanced=False)
    db = get_db()
    e = await db.entities.find_one(ws.scope(_id=oid(entity_id, what="entity")))
    if not e:
        raise NotFound("Entity not found.")
    page, size, skip = paging(mentions_page, 10, 20)
    mentions = [m["chunk_id"] async for m in db.entity_mentions.find(ws.scope(entity_id=e["_id"])).sort("_id", 1).skip(skip).limit(size)]
    rels = [
        r
        async for r in db.relationships.find(ws.scope(**{"$or": [{"source_entity_id": e["_id"]}, {"target_entity_id": e["_id"]}]}))
        .sort("evidence_count", -1)
        .limit(50)
    ]
    other_ids = {r["target_entity_id"] if r["source_entity_id"] == e["_id"] else r["source_entity_id"] for r in rels}
    names = {x["_id"]: x["name"] async for x in db.entities.find(ws.scope(_id={"$in": list(other_ids)}), projection={"name": 1})}
    sources = []
    for coll, kind in ((db.books, "book"), (db.documents, "document")):
        async for s in coll.find(ws.scope(_id={"$in": e.get("source_ids", [])[:50]}), projection={"title": 1}):
            sources.append({"id": str(s["_id"]), "title": s.get("title"), "kind": kind})
    return {
        **entity_out(e),
        "sources": sources,
        "mentions": await _evidence(ws, mentions),
        "mentions_page": page,
        "relationships": [
            {
                **edge_out(r),
                "direction": "out" if r["source_entity_id"] == e["_id"] else "in",
                "other": {
                    "id": str(oid_ := (r["target_entity_id"] if r["source_entity_id"] == e["_id"] else r["source_entity_id"])),
                    "name": names.get(oid_, "?"),
                },
            }
            for r in rels
        ],
    }


@router.get("/relationships/{rel_id}")
async def get_relationship(rel_id: str, ws: Workspace) -> dict[str, Any]:
    await _gate(ws, advanced=False)
    db = get_db()
    r = await db.relationships.find_one(ws.scope(_id=oid(rel_id, what="relationship")))
    if not r:
        raise NotFound("Relationship not found.")
    ents = {x["_id"]: entity_out(x) async for x in db.entities.find(ws.scope(_id={"$in": [r["source_entity_id"], r["target_entity_id"]]}))}
    ev = r.get("evidence", [])
    return {
        **edge_out(r),
        "source_entity": ents.get(r["source_entity_id"]),
        "target_entity": ents.get(r["target_entity_id"]),
        "descriptions": sorted({e.get("description") for e in ev if e.get("description")})[:10],
        "evidence": await _evidence(ws, [e["chunk_id"] for e in ev]),
        "disclaimer": "AI-extracted relationship. Verify against the cited passages.",
    }


async def _subgraph(
    ws: Any,
    seeds: list[ObjectId],
    *,
    depth: int,
    limit: int,
    types: list[str] | None,
    rel_types: list[str] | None,
    source_id: ObjectId | None,
) -> dict[str, Any]:
    db = get_db()
    nodes: set[ObjectId] = set(seeds)
    edges: dict[ObjectId, dict[str, Any]] = {}
    frontier = list(seeds)
    truncated = False
    for _ in range(depth):
        if not frontier:
            break
        q: dict[str, Any] = ws.scope(**{"$or": [{"source_entity_id": {"$in": frontier}}, {"target_entity_id": {"$in": frontier}}]})
        if rel_types:
            q["type"] = {"$in": rel_types}
        if source_id:
            q["evidence.source_id"] = source_id
        nxt: list[ObjectId] = []
        async for r in db.relationships.find(q).sort("evidence_count", -1).limit(MAX_EDGES):
            for end in (r["source_entity_id"], r["target_entity_id"]):
                if end not in nodes:
                    if len(nodes) >= limit:
                        truncated = True
                        continue
                    nodes.add(end)
                    nxt.append(end)
            if r["source_entity_id"] in nodes and r["target_entity_id"] in nodes:
                edges[r["_id"]] = r
        frontier = nxt
    ent_q: dict[str, Any] = ws.scope(_id={"$in": list(nodes)})
    if types:
        ent_q["$or"] = [{"type": {"$in": types}}, {"_id": {"$in": seeds}}]
    ents = {e["_id"]: e async for e in db.entities.find(ent_q)}
    kept_edges = [edge_out(r) for r in edges.values() if r["source_entity_id"] in ents and r["target_entity_id"] in ents]
    return {
        "nodes": [entity_out(e) for e in ents.values()],
        "edges": kept_edges[:MAX_EDGES],
        "truncated": truncated,
        "limits": {"max_nodes": limit, "max_edges": MAX_EDGES},
    }


@router.get("/overview")
async def overview(
    ws: Workspace, limit: int = Query(40, ge=5, le=100), type: list[str] | None = Query(None), source_id: str | None = None
) -> dict[str, Any]:
    await _gate(ws, advanced=bool(source_id))
    flt: dict[str, Any] = ws.scope()
    if type:
        flt["type"] = {"$in": [t for t in type if t in ENTITY_TYPES]}
    sid = maybe_oid(source_id) if source_id else None
    if sid:
        flt["source_ids"] = sid
    seeds = [e["_id"] async for e in get_db().entities.find(flt, projection={"_id": 1}).sort("mention_count", -1).limit(limit)]
    return await _subgraph(ws, seeds, depth=1, limit=min(MAX_NODES, limit * 2), types=type, rel_types=None, source_id=sid)


@router.get("/neighborhood/{entity_id}")
async def neighborhood(
    entity_id: str,
    ws: Workspace,
    depth: int = Query(1, ge=1, le=2),
    limit: int = Query(60, ge=5, le=MAX_NODES),
    type: list[str] | None = Query(None),
    rel_type: list[str] | None = Query(None),
    source_id: str | None = None,
) -> dict[str, Any]:
    advanced = depth > 1 or bool(rel_type) or bool(source_id)
    await _gate(ws, advanced=advanced)
    e = await get_db().entities.find_one(ws.scope(_id=oid(entity_id, what="entity")), projection={"_id": 1})
    if not e:
        raise NotFound("Entity not found.")
    return await _subgraph(
        ws,
        [e["_id"]],
        depth=depth,
        limit=limit,
        types=type,
        rel_types=[t for t in (rel_type or []) if t in REL_TYPES] or None,
        source_id=maybe_oid(source_id) if source_id else None,
    )
