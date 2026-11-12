"""Atlas Vector Search and Atlas Search index definitions and setup.

These indexes only exist on MongoDB Atlas (or deployments running mongot). On a plain
mongod the setup reports clearly that search indexes are unavailable instead of
pretending they work.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from app.core.config import get_settings
from app.retrieval.spaces import marker_for

FILTER_FIELDS = ("workspace_id", "file_id", "source_id", "source_type", "language")


def vector_index_definition(path: str | None = None, dims: int | None = None) -> dict[str, Any]:
    s = get_settings()
    vf = path or s.vector_field
    return {
        "fields": [
            {"type": "vector", "path": vf, "numDimensions": dims or s.gemini_embedding_dimensions, "similarity": "cosine"},
            {"type": "filter", "path": marker_for(vf)},
            *({"type": "filter", "path": f} for f in FILTER_FIELDS),
        ]
    }


def text_index_definition() -> dict[str, Any]:
    text_field = {"type": "string", "analyzer": "lucene.standard", "multi": {"english": {"type": "string", "analyzer": "lucene.english"}}}
    return {
        "mappings": {
            "dynamic": False,
            "fields": {
                "text": text_field,
                "section_title": {"type": "string", "analyzer": "lucene.standard"},
                "source_title": {"type": "string", "analyzer": "lucene.standard"},
                "author": {"type": "string", "analyzer": "lucene.standard"},
                "workspace_id": {"type": "objectId"},
                "file_id": {"type": "objectId"},
                "source_id": {"type": "objectId"},
                "source_type": {"type": "token"},
                marker_for(get_settings().vector_field): {"type": "token"},
                "language": {"type": "token"},
            },
        }
    }


async def _status(db: AsyncDatabase[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    async for idx in await db.chunks.list_search_indexes():
        out[idx["name"]] = idx.get("status") or ("READY" if idx.get("queryable") else "PENDING")
    return out


async def ensure_search_indexes(db: AsyncDatabase[Any], *, wait: bool = False, timeout: float = 600) -> dict[str, str]:
    s = get_settings()
    wanted = {
        s.atlas_vector_index_name: ("vectorSearch", vector_index_definition()),
        s.atlas_search_index_name: ("search", text_index_definition()),
    }
    try:
        existing = await _status(db)
    except OperationFailure as exc:
        return {name: f"UNAVAILABLE: Atlas Search is not available on this deployment ({exc.code})" for name in wanted}
    result: dict[str, str] = {}
    for name, (kind, definition) in wanted.items():
        if name in existing:
            result[name] = f"exists ({existing[name]})"
            continue
        try:
            await db.chunks.create_search_index(SearchIndexModel(definition=definition, name=name, type=kind))
            result[name] = "created"
        except OperationFailure as exc:
            result[name] = f"FAILED: {exc.details.get('errmsg') if exc.details else exc}"
    if wait:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            st = await _status(db)
            if all(st.get(n) == "READY" for n in wanted):
                return {n: "READY" for n in wanted}
            await asyncio.sleep(5)
        result.update({n: f"{v} (not READY after {int(timeout)}s)" for n, v in (await _status(db)).items() if n in wanted})
    return result


def definitions_json() -> str:
    s = get_settings()
    return json.dumps(
        {
            "vector": {
                "name": s.atlas_vector_index_name,
                "type": "vectorSearch",
                "collection": "chunks",
                "definition": vector_index_definition(),
            },
            "text": {"name": s.atlas_search_index_name, "type": "search", "collection": "chunks", "definition": text_index_definition()},
        },
        indent=2,
    )
