"""Hybrid retrieval: vector + keyword, fused with Reciprocal Rank Fusion, de-duplicated.

Every query is scoped to the caller's workspace and to the *active* embedding space; a
query vector is never compared with vectors from another model or dimensionality.
Degraded modes (one retriever unavailable) are reported to the caller, never hidden.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

from bson import ObjectId
from pymongo.errors import OperationFailure

from app.core.config import get_settings
from app.core.errors import AppError, ServiceUnavailable
from app.database.client import get_db
from app.documents.chunking import estimate_tokens
from app.retrieval.spaces import active_marker
from app.services.system_settings import get_runtime_settings

log = logging.getLogger("app.retrieval")

RRF_K = 60
EXACT_SCAN_LIMIT = 20000
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class RetrievalFilters:
    source_ids: list[ObjectId] = field(default_factory=list)
    file_ids: list[ObjectId] = field(default_factory=list)
    collection_id: ObjectId | None = None
    source_type: str | None = None
    language: str | None = None
    author: str | None = None
    date_from: str | None = None
    date_to: str | None = None


@dataclass
class Hit:
    id: ObjectId
    text: str
    file_id: ObjectId
    source_id: ObjectId
    source_type: str
    source_title: str
    section_path: list[str]
    page_start: int | None
    page_end: int | None
    citation_label: str
    ordinal: int
    overlap_prefix_chars: int = 0
    scores: dict[str, float] = field(default_factory=dict)
    rrf: float = 0.0

    @property
    def body(self) -> str:
        return self.text[self.overlap_prefix_chars :] if self.overlap_prefix_chars else self.text

    def to_public(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.id),
            "source_id": str(self.source_id),
            "file_id": str(self.file_id),
            "source_type": self.source_type,
            "source_title": self.source_title,
            "section_path": self.section_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "citation_label": self.citation_label,
            "snippet": self.body[:400],
            "scores": {k: round(v, 5) for k, v in self.scores.items()},
        }


@dataclass
class RetrievalResult:
    hits: list[Hit]
    degraded: list[str]
    timings_ms: dict[str, float]
    counts: dict[str, int]


_PROJECTION = {
    "text": 1,
    "file_id": 1,
    "source_id": 1,
    "source_type": 1,
    "source_title": 1,
    "section_path": 1,
    "page_start": 1,
    "page_end": 1,
    "citation_label": 1,
    "ordinal": 1,
    "overlap_prefix_chars": 1,
}


def _hit(d: dict[str, Any]) -> Hit:
    return Hit(
        id=d["_id"],
        text=d["text"],
        file_id=d["file_id"],
        source_id=d["source_id"],
        source_type=d.get("source_type", "document"),
        source_title=d.get("source_title") or "Untitled",
        section_path=d.get("section_path") or [],
        page_start=d.get("page_start"),
        page_end=d.get("page_end"),
        citation_label=d.get("citation_label") or "",
        ordinal=int(d.get("ordinal", 0)),
        overlap_prefix_chars=int(d.get("overlap_prefix_chars", 0)),
    )


async def _resolve_file_scope(workspace_id: ObjectId, f: RetrievalFilters) -> list[ObjectId] | None:
    """Translate source/collection/author/date filters into an authorised file-id allow-list."""
    db = get_db()
    if not (f.source_ids or f.file_ids or f.collection_id or f.author or f.date_from or f.date_to):
        return None
    ids: set[ObjectId] | None = None

    def intersect(new: set[ObjectId]) -> None:
        nonlocal ids
        ids = new if ids is None else ids & new

    if f.file_ids:
        intersect(
            {x["_id"] async for x in db.files.find({"workspace_id": workspace_id, "_id": {"$in": f.file_ids}}, projection={"_id": 1})}
        )
    if f.source_ids:
        found: set[ObjectId] = set()
        for coll in (db.books, db.documents):
            found |= {
                x["file_id"]
                async for x in coll.find({"workspace_id": workspace_id, "_id": {"$in": f.source_ids}}, projection={"file_id": 1})
            }
        intersect(found)
    if f.collection_id:
        intersect(
            {
                x["_id"]
                async for x in db.files.find({"workspace_id": workspace_id, "collection_ids": f.collection_id}, projection={"_id": 1})
            }
        )
    if f.author or f.date_from or f.date_to:
        q: dict[str, Any] = {"workspace_id": workspace_id}
        if f.author:
            q["author"] = {"$regex": re.escape(f.author), "$options": "i"}
        if f.date_from or f.date_to:
            q["publication_date"] = {k: v for k, v in (("$gte", f.date_from), ("$lte", f.date_to)) if v}
        found = set()
        for coll in (db.books, db.documents):
            found |= {x["file_id"] async for x in coll.find(q, projection={"file_id": 1})}
        intersect(found)
    return sorted(ids or set())


def _base_filter(workspace_id: ObjectId, space: str, file_scope: list[ObjectId] | None, f: RetrievalFilters) -> dict[str, Any]:
    q: dict[str, Any] = {"workspace_id": workspace_id, active_marker(): space}
    if file_scope is not None:
        q["file_id"] = {"$in": file_scope}
    if f.source_type:
        q["source_type"] = f.source_type
    if f.language:
        q["language"] = f.language
    return q


async def _vector(workspace_id: ObjectId, qvec: list[float], flt: dict[str, Any], k: int) -> list[Hit]:
    s = get_settings()
    db = get_db()
    if s.vector_search_backend == "atlas":
        stage = {
            "$vectorSearch": {
                "index": s.atlas_vector_index_name,
                "path": s.vector_field,
                "queryVector": qvec,
                "numCandidates": max(k * 10, 100),
                "limit": k,
                "filter": flt,
            }
        }
        cursor = await db.chunks.aggregate([stage, {"$project": {**_PROJECTION, "score": {"$meta": "vectorSearchScore"}}}])
        out = []
        async for d in cursor:
            h = _hit(d)
            h.scores["vector"] = float(d.get("score", 0))
            out.append(h)
        return out
    # Explicit development fallback: exact cosine similarity computed in the API process.
    scored: list[tuple[float, dict[str, Any]]] = []
    n = 0
    async for d in db.chunks.find(flt, projection={**_PROJECTION, s.vector_field: 1}).limit(EXACT_SCAN_LIMIT):
        n += 1
        v = d.get(s.vector_field) or []
        if len(v) != len(qvec):
            continue
        dot = sum(a * b for a, b in zip(v, qvec, strict=True))
        norm = math.sqrt(sum(a * a for a in v)) * math.sqrt(sum(b * b for b in qvec)) or 1.0
        scored.append((dot / norm, d))
    if n >= EXACT_SCAN_LIMIT:
        log.warning("exact_vector_scan_truncated", extra={"limit": EXACT_SCAN_LIMIT})
    scored.sort(key=lambda x: x[0], reverse=True)
    hits = []
    for score, d in scored[:k]:
        h = _hit(d)
        h.scores["vector"] = score
        hits.append(h)
    return hits


async def _keyword(workspace_id: ObjectId, query: str, flt: dict[str, Any], k: int) -> list[Hit]:
    s = get_settings()
    db = get_db()
    if s.keyword_search_backend == "atlas":
        filters: list[dict[str, Any]] = [
            {"equals": {"path": "workspace_id", "value": workspace_id}},
            {"equals": {"path": active_marker(), "value": flt[active_marker()]}},
        ]
        if "file_id" in flt:
            filters.append({"in": {"path": "file_id", "value": flt["file_id"]["$in"]}})
        for fld in ("source_type", "language"):
            if fld in flt:
                filters.append({"equals": {"path": fld, "value": flt[fld]}})
        stage = {
            "$search": {
                "index": s.atlas_search_index_name,
                "compound": {
                    "filter": filters,
                    "should": [
                        {"text": {"query": query, "path": "text"}},
                        {"text": {"query": query, "path": {"value": "text", "multi": "english"}}},
                        {"phrase": {"query": query, "path": "text", "slop": 2, "score": {"boost": {"value": 2}}}},
                        {"text": {"query": query, "path": ["section_title", "source_title", "author"], "score": {"boost": {"value": 1.5}}}},
                    ],
                    "minimumShouldMatch": 1,
                },
            }
        }
        agg = await db.chunks.aggregate([stage, {"$limit": k}, {"$project": {**_PROJECTION, "score": {"$meta": "searchScore"}}}])
        out = []
        async for d in agg:
            h = _hit(d)
            h.scores["keyword"] = float(d.get("score", 0))
            out.append(h)
        return out
    cursor = (
        db.chunks.find({**flt, "$text": {"$search": query}}, projection={**_PROJECTION, "score": {"$meta": "textScore"}})
        .sort([("score", {"$meta": "textScore"})])
        .limit(k)
    )
    out = []
    async for d in cursor:
        h = _hit(d)
        h.scores["keyword"] = float(d.get("score", 0))
        out.append(h)
    return out


def _shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    w = _WORD.findall(text.lower())
    return {tuple(w[i : i + n]) for i in range(max(1, len(w) - n + 1))}


def rrf_fuse(lists: dict[str, list[Hit]], k: int = RRF_K) -> list[Hit]:
    by_id: dict[ObjectId, Hit] = {}
    for name, hits in lists.items():
        for rank, h in enumerate(hits, start=1):
            cur = by_id.setdefault(h.id, h)
            if cur is not h:
                cur.scores.update(h.scores)
            cur.rrf += 1.0 / (k + rank)
            cur.scores[f"{name}_rank"] = rank
    return sorted(by_id.values(), key=lambda h: h.rrf, reverse=True)


def dedupe(hits: list[Hit], *, per_page: int = 2, jaccard: float = 0.8) -> list[Hit]:
    """Drop near-duplicates and avoid many hits from the same page/neighbourhood."""
    kept: list[Hit] = []
    sigs: list[set[tuple[str, ...]]] = []
    page_counts: dict[tuple[ObjectId, Any], int] = {}
    for h in hits:
        if any(k.file_id == h.file_id and abs(k.ordinal - h.ordinal) == 0 for k in kept):
            continue
        sig = _shingles(h.body)
        if any(len(sig & s2) / max(1, len(sig | s2)) >= jaccard for s2 in sigs):
            continue
        key = (h.file_id, h.page_start if h.page_start is not None else f"o{h.ordinal // 3}")
        if page_counts.get(key, 0) >= per_page:
            continue
        page_counts[key] = page_counts.get(key, 0) + 1
        kept.append(h)
        sigs.append(sig)
    return kept


async def hybrid_search(
    workspace_id: ObjectId,
    query: str,
    *,
    filters: RetrievalFilters | None = None,
    top_k: int | None = None,
    candidates: int | None = None,
    use_vector: bool = True,
    account_id: Any = None,
) -> RetrievalResult:
    from app.embeddings import get_embedding_provider

    s = get_settings()
    rt = await get_runtime_settings()
    f = filters or RetrievalFilters()
    top_k = top_k or rt.retrieval_top_k
    candidates = candidates or rt.retrieval_candidates
    timings: dict[str, float] = {}
    degraded: list[str] = []
    t0 = time.perf_counter()
    scope = await _resolve_file_scope(workspace_id, f)
    if scope is not None and not scope:
        return RetrievalResult([], [], {"total": 0.0}, {"vector": 0, "keyword": 0, "fused": 0})
    flt = _base_filter(workspace_id, s.embedding_space_id, scope, f)

    lists: dict[str, list[Hit]] = {}
    if use_vector:
        try:
            t = time.perf_counter()
            qvec = await get_embedding_provider().embed_query(query, account_id=account_id)
            lists["vector"] = await _vector(workspace_id, qvec, flt, candidates)
            timings["vector"] = (time.perf_counter() - t) * 1000
        except (AppError, OperationFailure) as exc:
            log.warning("vector_retrieval_unavailable", extra={"error": getattr(exc, "code", type(exc).__name__)})
            degraded.append("vector_unavailable")
    try:
        t = time.perf_counter()
        lists["keyword"] = await _keyword(workspace_id, query, flt, candidates)
        timings["keyword"] = (time.perf_counter() - t) * 1000
    except OperationFailure as exc:
        log.warning("keyword_retrieval_unavailable", extra={"error": str(exc)[:200]})
        degraded.append("keyword_unavailable")
    if not lists:
        raise ServiceUnavailable("Search is temporarily unavailable. Please try again shortly.", code="retrieval_unavailable")
    fused = dedupe(rrf_fuse(lists))
    timings["total"] = (time.perf_counter() - t0) * 1000
    return RetrievalResult(
        hits=fused[: max(top_k, 1) * 3],
        degraded=degraded,
        timings_ms={k: round(v, 1) for k, v in timings.items()},
        counts={"vector": len(lists.get("vector", [])), "keyword": len(lists.get("keyword", [])), "fused": len(fused)},
    )


async def expand_with_neighbours(workspace_id: ObjectId, hits: list[Hit], *, min_tokens: int = 180) -> dict[ObjectId, str]:
    """Attach the following chunk when a hit is short, to give the model enough context."""
    db = get_db()
    extra: dict[ObjectId, str] = {}
    for h in hits:
        if estimate_tokens(h.body) >= min_tokens:
            continue
        nxt = await db.chunks.find_one(
            {"workspace_id": workspace_id, "file_id": h.file_id, "ordinal": h.ordinal + 1},
            projection={"text": 1, "overlap_prefix_chars": 1, "section_title": 1},
        )
        if nxt and (not h.section_path or nxt.get("section_title") == h.section_path[-1]):
            extra[h.id] = nxt["text"][int(nxt.get("overlap_prefix_chars", 0)) :]
    return extra


def select_context(hits: list[Hit], budget_tokens: int, extras: dict[ObjectId, str] | None = None) -> list[tuple[Hit, str]]:
    out: list[tuple[Hit, str]] = []
    used = 0
    for h in hits:
        text = h.body + (("\n" + extras[h.id]) if extras and h.id in extras else "")
        t = estimate_tokens(text) + 40
        if used + t > budget_tokens:
            if not out and budget_tokens > 200:  # always include at least a truncated top hit
                cut = int(len(text) * (budget_tokens - 40) / t)
                out.append((h, text[:cut]))
            break
        out.append((h, text))
        used += t
    return out
