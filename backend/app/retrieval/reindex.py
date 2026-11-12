"""Explicit, resumable re-indexing for embedding model / dimension / provider changes.

Workflow (docs/runbook.md, "Changing the embedding model"):
1. A SUPER_ADMIN starts a migration with the *target* model, dimensions and a NEW vector
   field (e.g. ``embedding_v2``). The active index keeps serving queries meanwhile.
2. The ``reindex.embed`` job embeds every chunk into the target field and records the
   target space in the field's marker (``embedding_v2_space``). It is resumable and
   cancellable and tracks progress, failures and remaining work.
3. Create the Atlas vector index for the target field (``setup-search-indexes`` with the
   new settings) and validate coverage via the admin API.
4. Switch the environment (VECTOR_FIELD, GEMINI_EMBEDDING_MODEL/DIMENSIONS,
   ATLAS_VECTOR_INDEX_NAME) and restart. Vectors from different spaces are never mixed.
Chunking-version changes are handled by re-processing files (ingestion re-chunks when the
stored chunking version differs).
"""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId
from pymongo import UpdateOne

from app.core.config import get_settings
from app.core.errors import ProviderError, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.documents.chunking import Chunk, embedding_text
from app.embeddings import get_embedding_provider
from app.embeddings.base import EmbeddingProvider
from app.jobs import queue
from app.jobs.runtime import JobContext, PermanentJobError, RetryableJobError, handler
from app.retrieval.spaces import marker_for

log = logging.getLogger("app.reindex")


def space_id(provider: str, model: str, dims: int) -> str:
    return f"{provider}:{model}:{dims}"


def provider_for(target: dict[str, Any]) -> EmbeddingProvider:
    s = get_settings()
    if target["space_id"] == s.embedding_space_id:
        return get_embedding_provider()
    if target["provider"] != "gemini":
        raise ValidationFailed(f"Embedding provider '{target['provider']}' is not implemented.")
    from app.embeddings.gemini import GeminiEmbeddings

    clone = s.model_copy(
        update={
            "gemini_embedding_model": target["model"],
            "gemini_embedding_dimensions": target["dimensions"],
            "embedding_space_id": target["space_id"],
        }
    )
    return GeminiEmbeddings(clone)


async def start_migration(
    *, provider: str, model: str, dimensions: int, vector_field: str, actor_id: ObjectId, note: str
) -> dict[str, Any]:
    s = get_settings()
    if not 128 <= dimensions <= 3072:
        raise ValidationFailed("Dimensions must be between 128 and 3072.")
    if vector_field == s.vector_field:
        raise ValidationFailed("Use a new vector field for the target space so the active index keeps working during migration.")
    if not vector_field.replace("_", "").isalnum() or len(vector_field) > 40:
        raise ValidationFailed("Invalid vector field name.")
    db = get_db()
    if await db.reindex_migrations.find_one({"status": {"$in": ["running", "queued"]}}):
        raise ValidationFailed("Another re-index migration is already running.")
    target = {
        "provider": provider,
        "model": model,
        "dimensions": dimensions,
        "vector_field": vector_field,
        "space_id": space_id(provider, model, dimensions),
    }
    total = await db.chunks.count_documents({})
    doc = {
        "target": target,
        "source": {"space_id": s.embedding_space_id, "vector_field": s.vector_field},
        "status": "queued",
        "total_chunks": total,
        "embedded": 0,
        "failed_batches": 0,
        "note": note,
        "created_by": actor_id,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    res = await db.reindex_migrations.insert_one(doc)
    job_id = await queue.enqueue(
        "reindex.embed",
        {"migration_id": str(res.inserted_id)},
        priority=-10,
        dedupe_key=f"reindex:{res.inserted_id}",
        timeout_seconds=6 * 3600,
        max_attempts=20,
    )
    await db.reindex_migrations.update_one({"_id": res.inserted_id}, {"$set": {"job_id": job_id}})
    from app.services import audit

    await audit.record(
        "reindex.started", actor_id=actor_id, actor_type="admin", target_type="reindex", target_id=res.inserted_id, details=target
    )
    return {**doc, "_id": res.inserted_id, "job_id": job_id}


async def coverage(migration_id: ObjectId) -> dict[str, Any]:
    db = get_db()
    m = await db.reindex_migrations.find_one({"_id": migration_id})
    if not m:
        raise ValidationFailed("Migration not found.")
    marker = marker_for(m["target"]["vector_field"])
    total = await db.chunks.count_documents({})
    done = await db.chunks.count_documents({marker: m["target"]["space_id"]})
    return {
        "total_chunks": total,
        "embedded": done,
        "remaining": total - done,
        "complete": total == done,
        "target": m["target"],
        "status": m["status"],
    }


@handler("reindex.embed")
async def reindex_embed(ctx: JobContext) -> dict[str, Any]:
    db = get_db()
    mid = ObjectId(ctx.payload["migration_id"])
    m = await db.reindex_migrations.find_one({"_id": mid})
    if not m or m["status"] in ("completed", "cancelled"):
        return {"skipped": True}
    await db.reindex_migrations.update_one({"_id": mid}, {"$set": {"status": "running", "updated_at": utcnow()}})
    target = m["target"]
    provider = provider_for(target)
    vf, marker = target["vector_field"], marker_for(target["vector_field"])
    done = await db.chunks.count_documents({marker: target["space_id"]})
    while True:
        await ctx.checkpoint()
        batch = [
            c
            async for c in db.chunks.find(
                {marker: {"$ne": target["space_id"]}},
                projection={"text": 1, "section_path": 1, "section_title": 1, "source_title": 1, "owner_id": 1},
            ).limit(64)
        ]
        if not batch:
            break
        texts = [
            embedding_text(c.get("source_title"), Chunk(0, c["text"], c.get("section_title", ""), c.get("section_path", []), None, None))
            for c in batch
        ]
        try:
            res = await provider.embed_documents(texts, job_id=ctx.id)
        except ProviderError as exc:
            await db.reindex_migrations.update_one(
                {"_id": mid}, {"$inc": {"failed_batches": 1}, "$set": {"last_error": exc.code, "updated_at": utcnow()}}
            )
            if exc.retryable:
                raise RetryableJobError(exc.code, exc.message) from exc
            raise PermanentJobError(exc.code, exc.message) from exc
        now = utcnow()
        await db.chunks.bulk_write(
            [
                UpdateOne({"_id": c["_id"]}, {"$set": {vf: v, marker: target["space_id"], f"{vf}_embedded_at": now}})
                for c, v in zip(batch, res.vectors, strict=True)
            ],
            ordered=False,
        )
        done += len(batch)
        await db.reindex_migrations.update_one({"_id": mid}, {"$set": {"embedded": done, "updated_at": now}})
        await ctx.emit("progress", {"embedded": done}, completed_steps=done, total_steps=m["total_chunks"])
    await db.reindex_migrations.update_one(
        {"_id": mid}, {"$set": {"status": "completed", "embedded": done, "completed_at": utcnow(), "updated_at": utcnow()}}
    )
    return {"embedded": done}
