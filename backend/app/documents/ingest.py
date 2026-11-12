"""Ingestion pipeline: parse → normalise → detect language → section/chunk → embed → index.

Idempotent and resumable: re-running after a crash re-uses already persisted chunks whose
content hashes match and embeds only chunks still missing a vector for the active space.
Once a file is indexed its original is deleted, unless administrators keep originals
(app.documents.originals).
"""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId
from pymongo import UpdateOne

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.documents.chunking import Chunk, chunk_document, embedding_text
from app.documents.isolation import parse_isolated
from app.documents.language import detect_language
from app.documents.originals import original_available, purge_original
from app.documents.parsers.base import ParsedDocument, ParseError
from app.embeddings import get_embedding_provider
from app.embeddings.base import EmbeddingModelMetadata
from app.jobs import queue
from app.jobs.runtime import JobContext, PermanentJobError, RetryableJobError, handler, on_permanent_failure
from app.retrieval.spaces import marker_for
from app.services.system_settings import get_runtime_settings
from app.storage import storage_for

log = logging.getLogger("app.ingest")

PARSE_ISOLATION = True  # tests may disable to avoid process spawn overhead


def _decide_kind(requested: str | None, parsed: ParsedDocument) -> str:
    if requested in ("book", "document"):
        return requested
    chapters = sum(1 for s in parsed.sections if s.level == 1)
    if (parsed.page_count or 0) >= 60 or (chapters >= 5 and parsed.char_count > 150_000):
        return "book"
    return "document"


async def _set_file(file_id: ObjectId, **fields: Any) -> None:
    await get_db().files.update_one({"_id": file_id}, {"$set": {**fields, "updated_at": utcnow()}})


@handler("ingest.file")
async def ingest_file(ctx: JobContext) -> dict[str, Any]:
    s = get_settings()
    db = get_db()
    file_id = ObjectId(ctx.payload["file_id"])
    f = await db.files.find_one({"_id": file_id})
    if not f or f["storage_status"] != "stored":
        return {"skipped": "file_not_available"}
    if (
        f.get("processing_status") == "indexed"
        and f.get("embedding_space") == s.embedding_space_id
        and f.get("chunking_version") == s.chunking_version
    ):
        return {"skipped": "already_indexed"}
    if f.get("original_purged_at"):
        return await _reembed_without_original(ctx, f)
    await _set_file(file_id, processing_status="processing", processing_error=None)
    await ctx.emit("phase", {"phase": "parsing"}, phase="parsing")

    try:
        data = await storage_for(f).read_bytes(f["storage_key"], max_bytes=s.upload_hard_max_bytes)
    except FileNotFoundError as exc:
        raise PermanentJobError("storage_missing", "The stored file could not be found.") from exc
    except OSError as exc:
        raise RetryableJobError("storage_unavailable", "Object storage is temporarily unavailable.") from exc

    try:
        parsed = await parse_isolated(data, f["file_type"], f["original_filename"], isolate=PARSE_ISOLATION)
    except ParseError as exc:
        if exc.retryable:
            raise RetryableJobError(exc.code, exc.message) from exc
        raise PermanentJobError(exc.code, exc.message) from exc
    if parsed.needs_ocr:
        raise PermanentJobError(
            "ocr_required",
            "This PDF contains scanned page images without a text layer. OCR is required to index it, and OCR is not enabled on this server.",
        )
    if parsed.char_count == 0:
        raise PermanentJobError("empty_document", "No readable text was found in this file.")
    await ctx.checkpoint()

    sample = " ".join(b.text for sec in parsed.sections[:20] for b in sec.blocks[:20])[:20000]
    lang, conf = detect_language(sample)
    parsed.language = parsed.language or lang
    kind = _decide_kind(f.get("requested_kind"), parsed)
    now = utcnow()
    source_coll = db.books if kind == "book" else db.documents
    other_coll = db.documents if kind == "book" else db.books
    await other_coll.delete_one({"file_id": file_id})  # in case a previous attempt classified differently
    metadata = {
        "title": {"value": parsed.title or f["display_title"], "provenance": "extracted" if parsed.title else "filename"},
        "author": {"value": parsed.author, "provenance": "extracted"} if parsed.author else None,
        "publisher": {"value": parsed.publisher, "provenance": "extracted"} if parsed.publisher else None,
        "publication_date": {"value": parsed.publication_date, "provenance": "extracted"} if parsed.publication_date else None,
        "language": {"value": parsed.language, "provenance": "detected", "confidence": conf} if parsed.language else None,
    }
    src = await source_coll.find_one_and_update(
        {"file_id": file_id},
        {
            "$set": {
                "workspace_id": f["workspace_id"],
                "owner_id": f["owner_id"],
                "file_id": file_id,
                "title": f["display_title"] if f.get("title_source") == "user" else (parsed.title or f["display_title"]),
                "author": parsed.author,
                "publisher": parsed.publisher,
                "publication_date": parsed.publication_date,
                "language": parsed.language,
                "page_count": parsed.page_count,
                "file_type": parsed.file_type,
                "outline": parsed.outline(),
                "metadata": metadata,
                "parse_warnings": parsed.warnings[:20],
                "collection_ids": f.get("collection_ids", []),
                "status": "indexing",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        return_document=True,
    )
    assert src is not None  # upsert always returns a document
    source_id = src["_id"]
    title_fields: dict[str, Any] = {}
    if parsed.title and f.get("title_source") != "user":
        # Show the document's own title in the library instead of the filename.
        title_fields = {"display_title": parsed.title[:200], "title_source": "extracted"}
    await _set_file(file_id, kind=kind, source_id=source_id, source_type=kind, language=parsed.language, **title_fields)
    await ctx.emit("phase", {"phase": "chunking", "sections": len(parsed.sections)}, phase="chunking")

    try:
        chunks = chunk_document(
            parsed,
            target_tokens=s.chunk_target_tokens,
            overlap_tokens=s.chunk_overlap_tokens,  # gitleaks:allow (token counts, not a secret)
            max_chunks=s.max_chunks_per_ingestion,
        )
    except ValueError as exc:
        raise PermanentJobError("too_many_chunks", f"This file is too large to index ({exc}).") from exc

    existing = [
        c
        async for c in db.chunks.find({"file_id": file_id}, projection={"content_hash": 1, "ordinal": 1, "chunking_version": 1}).sort(
            "ordinal", 1
        )
    ]
    same = len(existing) == len(chunks) and all(
        e["content_hash"] == c.content_hash and e.get("chunking_version") == s.chunking_version
        for e, c in zip(existing, chunks, strict=False)
    )
    title = src.get("title") or f["display_title"]
    if not same:
        await db.chunks.delete_many({"file_id": file_id})
        docs = [
            {
                "workspace_id": f["workspace_id"],
                "owner_id": f["owner_id"],
                "file_id": file_id,
                "source_id": source_id,
                "source_type": kind,
                "source_title": title,
                "author": parsed.author,
                "ordinal": c.ordinal,
                "text": c.text,
                "section_title": c.section_title,
                "section_path": c.section_path,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "locations": c.locations,
                "token_estimate": c.token_estimate,
                "content_hash": c.content_hash,
                "overlap_prefix_chars": c.overlap_prefix_chars,
                "citation_label": c.citation_label(),
                "chunking_version": s.chunking_version,
                "language": parsed.language,
                marker_for(s.vector_field): None,
                "created_at": now,
            }
            for c in chunks
        ]
        for i in range(0, len(docs), 1000):
            await db.chunks.insert_many(docs[i : i + 1000], ordered=False)
    meta = await _embed_pending(ctx, f, title, len(chunks))
    await _finish(ctx, f, kind=kind, source_id=source_id, chunk_count=len(chunks), space_id=meta.space_id, chunked=True)
    return {"chunks": len(chunks), "kind": kind, "language": parsed.language, "source_id": str(source_id)}


async def _embed_pending(ctx: JobContext, f: dict[str, Any], title: str, total: int) -> EmbeddingModelMetadata:
    """Embed every chunk of the file that has no vector in the active space yet."""
    s = get_settings()
    db = get_db()
    file_id = f["_id"]
    await ctx.emit("phase", {"phase": "embedding", "chunks": total}, phase="embedding", total_steps=total, completed_steps=0)
    provider = get_embedding_provider()
    meta = provider.get_model_metadata()
    vf = s.vector_field
    marker = marker_for(vf)
    done = await db.chunks.count_documents({"file_id": file_id, marker: meta.space_id})
    batch_size = 64
    while True:
        await ctx.checkpoint()
        pending = [
            c
            async for c in db.chunks.find(
                {"file_id": file_id, marker: {"$ne": meta.space_id}},
                projection={"text": 1, "section_path": 1, "section_title": 1, "page_start": 1, "page_end": 1},
            )
            .sort("ordinal", 1)
            .limit(batch_size)
        ]
        if not pending:
            break
        texts = [
            embedding_text(
                title,
                Chunk(
                    ordinal=0,
                    text=c["text"],
                    section_title=c.get("section_title", ""),
                    section_path=c.get("section_path", []),
                    page_start=c.get("page_start"),
                    page_end=c.get("page_end"),
                ),
            )
            for c in pending
        ]
        try:
            result = await provider.embed_documents(texts, account_id=f["owner_id"], job_id=ctx.id)
        except ProviderError as exc:
            if (
                exc.retryable
                or exc.status_code >= 500
                and exc.code not in ("provider_not_configured", "provider_auth_failed", "provider_model_not_found")
            ):
                raise RetryableJobError(exc.code, exc.message) from exc
            raise PermanentJobError(exc.code, exc.message) from exc
        ts = utcnow()
        ops = [
            UpdateOne(
                {"_id": c["_id"]},
                {
                    "$set": {
                        vf: vec,
                        marker: meta.space_id,
                        "embedding_provider": meta.provider,
                        "embedding_model": meta.model,
                        "embedding_dims": meta.dimensions,
                        "embedded_at": ts,
                    }
                },
            )
            for c, vec in zip(pending, result.vectors, strict=True)
        ]
        await db.chunks.bulk_write(ops, ordered=False)
        done += len(pending)
        await ctx.emit("progress", {"embedded": done, "total": total}, completed_steps=done)
    return meta


async def _finish(
    ctx: JobContext, f: dict[str, Any], *, kind: str | None, source_id: Any, chunk_count: int, space_id: str, chunked: bool
) -> None:
    s = get_settings()
    db = get_db()
    rt = await get_runtime_settings()
    file_id = f["_id"]
    purge = original_available(f) and not rt.keep_original_files
    fields: dict[str, Any] = {
        "processing_status": "indexed",
        "chunk_count": chunk_count,
        "embedding_space": space_id,
        "indexed_at": utcnow(),
        "updated_at": utcnow(),
        "processing_error": None,
    }
    if chunked:
        fields["chunking_version"] = s.chunking_version
    if purge:
        # Recorded with the status change, so a crash before the delete is picked up by maintenance.
        fields["original_purge_pending"] = True
    await db.files.update_one({"_id": file_id}, {"$set": fields})
    if source_id:
        source_coll = db.books if kind == "book" else db.documents
        await source_coll.update_one({"_id": source_id}, {"$set": {"status": "ready", "chunk_count": chunk_count, "updated_at": utcnow()}})
    if purge:
        await purge_original(f)
    if rt.graph_extraction_enabled:
        version = s.chunking_version if chunked else f.get("chunking_version") or s.chunking_version
        await queue.enqueue(
            "graph.extract",
            {"file_id": str(file_id)},
            account_id=f["owner_id"],
            workspace_id=f["workspace_id"],
            priority=-1,
            dedupe_key=f"graph:{file_id}:{version}",
            timeout_seconds=3600,
        )
    await ctx.emit("phase", {"phase": "completed", "chunks": chunk_count}, phase="indexed")


async def _reembed_without_original(ctx: JobContext, f: dict[str, Any]) -> dict[str, Any]:
    """The original was deleted after indexing, so the file can't be parsed or chunked again.
    Its stored passages can still be embedded into the active space."""
    db = get_db()
    file_id = f["_id"]
    total = await db.chunks.count_documents({"file_id": file_id})
    if not total:
        raise PermanentJobError("original_not_kept", "The original file is no longer on the server. Upload it again to index it.")
    first = await db.chunks.find_one({"file_id": file_id}, projection={"source_title": 1}) or {}
    await _set_file(file_id, processing_status="processing", processing_error=None)
    meta = await _embed_pending(ctx, f, first.get("source_title") or f["display_title"], total)
    await _finish(ctx, f, kind=f.get("kind"), source_id=f.get("source_id"), chunk_count=total, space_id=meta.space_id, chunked=False)
    return {"chunks": total, "reembedded": True}


@on_permanent_failure("ingest.file")
async def _ingest_failed(job: dict[str, Any], code: str, message: str) -> None:
    file_id = ObjectId(job["payload"]["file_id"])
    await get_db().files.update_one(
        {"_id": file_id, "storage_status": "stored"},
        {"$set": {"processing_status": "failed", "processing_error": {"code": code, "message": message}, "updated_at": utcnow()}},
    )
    for coll in (get_db().books, get_db().documents):
        await coll.update_one({"file_id": file_id}, {"$set": {"status": "failed"}})
