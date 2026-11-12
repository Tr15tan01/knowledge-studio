"""Dependency health checks. A required dependency that is down is reported as down."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.config import get_settings
from app.database.client import mongo


async def _timed(coro: Any, timeout: float = 5.0) -> tuple[bool, str, float]:
    t = time.perf_counter()
    try:
        ok, detail = await asyncio.wait_for(coro, timeout)
    except TimeoutError:
        ok, detail = False, "timeout"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, type(exc).__name__
    return ok, detail, round((time.perf_counter() - t) * 1000, 1)


async def _mongo() -> tuple[bool, str]:
    return (await mongo.ping(), "ok")


async def _storage() -> tuple[bool, str]:
    from app.storage import active_storage

    return await (await active_storage()).health()


def vector_index_problem(index: dict[str, Any], *, vector_field: str, dimensions: int) -> str | None:
    """Return why an Atlas vector index can't serve the configured embedding space, if it can't."""
    definition = index.get("latestDefinition") or index.get("definition") or {}
    vectors = [f for f in definition.get("fields", []) if f.get("type") == "vector"]
    match = next((f for f in vectors if f.get("path") == vector_field), None)
    if match is None:
        return f"vector index has no vector field '{vector_field}'"
    if int(match.get("numDimensions") or 0) != dimensions:
        return f"vector index has {match.get('numDimensions')} dimensions but GEMINI_EMBEDDING_DIMENSIONS={dimensions}"
    return None


async def _search_indexes() -> tuple[bool, str]:
    s = get_settings()
    if s.vector_search_backend != "atlas":
        return True, "exact (development fallback) — Atlas Vector Search not in use"
    try:
        names = {}
        docs: dict[str, dict[str, Any]] = {}
        async for idx in await mongo.db.chunks.list_search_indexes():
            names[idx.get("name")] = idx.get("status") or ("READY" if idx.get("queryable") else "PENDING")
            docs[str(idx.get("name"))] = dict(idx)
    except Exception as exc:  # noqa: BLE001
        return False, f"search indexes unavailable: {type(exc).__name__}"
    vec = names.get(s.atlas_vector_index_name)
    txt = names.get(s.atlas_search_index_name)
    if vec and (
        problem := vector_index_problem(
            docs[s.atlas_vector_index_name], vector_field=s.vector_field, dimensions=s.gemini_embedding_dimensions
        )
    ):
        return False, f"configuration mismatch: {problem}"
    ok = vec == "READY" and (s.keyword_search_backend != "atlas" or txt == "READY")
    return ok, f"vector={vec or 'missing'} text={txt or 'missing' if s.keyword_search_backend == 'atlas' else 'n/a'}"


async def _queue() -> tuple[bool, str]:
    from app.jobs.queue import queue_stats

    st = await queue_stats()
    workers_alive = await mongo.db.worker_heartbeats.count_documents({}) if mongo.db is not None else 0
    return (
        True,
        f"queued={st['by_status'].get('queued', 0)} running={st['by_status'].get('running', 0)} stuck={st['stuck']} workers={workers_alive}",
    )


async def readiness(deep: bool = False) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    ok, detail, ms = await _timed(_mongo())
    checks["mongodb"] = {"ok": ok, "detail": detail, "latency_ms": ms, "required": True}
    if ok:
        ok2, detail2, ms2 = await _timed(_storage())
        checks["storage"] = {"ok": ok2, "detail": detail2, "latency_ms": ms2, "required": True}
        if deep:
            ok3, d3, ms3 = await _timed(_search_indexes())
            checks["search_indexes"] = {"ok": ok3, "detail": d3, "latency_ms": ms3, "required": False}
            ok4, d4, ms4 = await _timed(_queue())
            checks["queue"] = {"ok": ok4, "detail": d4, "latency_ms": ms4, "required": False}
    ready = all(c["ok"] for c in checks.values() if c["required"])
    return {"ready": ready, "checks": checks}
