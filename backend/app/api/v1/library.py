"""Library: uploads, files, books, documents, collections, source preview, downloads."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import Field

from app.auth.deps import Workspace
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.core.units import format_bytes
from app.database.client import get_db
from app.database.ids import maybe_oid, oid
from app.documents.deletion import request_file_deletion
from app.documents.originals import original_available
from app.documents.upload import accept_upload
from app.jobs import queue
from app.retrieval.spaces import active_marker
from app.schemas.common import APIModel, iso, paging

router = APIRouter(tags=["library"])

PROCESSING_STATES = ("uploading", "queued", "processing")


def file_out(f: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    status = f.get("processing_status")
    if job and job.get("status") == "retrying" and status == "processing":
        status = "retrying"
    return {
        "id": str(f["_id"]),
        "title": f.get("display_title"),
        "filename": f.get("original_filename"),
        "kind": f.get("kind"),
        "file_type": f.get("file_type"),
        "size_bytes": int(f.get("original_size_bytes") or 0),
        "size_display": format_bytes(int(f.get("original_size_bytes") or 0)),
        "storage_status": f.get("storage_status"),
        "original_available": original_available(f),
        "processing_status": status,
        "processing_error": f.get("processing_error")
        or ({"code": f.get("failure_reason"), "message": None} if f.get("failure_reason") else None),
        "progress": (job or {}).get("progress"),
        "job_id": str(f["ingest_job_id"]) if f.get("ingest_job_id") else None,
        "source_id": str(f["source_id"]) if f.get("source_id") else None,
        "language": f.get("language"),
        "chunk_count": f.get("chunk_count", 0),
        "collection_ids": [str(c) for c in f.get("collection_ids", [])],
        "created_at": iso(f.get("created_at")),
        "indexed_at": iso(f.get("indexed_at")),
    }


@router.post("/files/upload", status_code=202)
async def upload(
    request: Request,
    ws: Workspace,
    x_filename: str = Header(..., max_length=600),
    kind: Literal["book", "document", "auto"] = Query("auto"),
    title: str | None = Query(None, max_length=200),
    collection_id: str | None = Query(None),
    idempotency_key: str | None = Header(None, max_length=100),
) -> dict[str, Any]:
    coll = None
    if collection_id:
        coll = oid(collection_id, what="collection")
        if not await get_db().collections.find_one(ws.scope(_id=coll)):
            raise NotFound("Collection not found.")
    res = await accept_upload(ws, request, filename=x_filename, kind=kind, title=title, collection_id=coll, idempotency_key=idempotency_key)
    return {"file": file_out(res["file"]), "job_id": str(res["job_id"]) if res["job_id"] else None, "replayed": res["replayed"]}


@router.get("/files")
async def list_files(
    ws: Workspace,
    page: int = 1,
    page_size: int = 20,
    status: Literal["all", "processing", "indexed", "failed", "attention"] = "all",
    kind: Literal["all", "book", "document"] = "all",
    q: str | None = Query(None, max_length=200),
    collection_id: str | None = None,
    sort: Literal["newest", "oldest", "title", "size"] = "newest",
) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt: dict[str, Any] = ws.scope(storage_status={"$in": ["uploading", "stored", "rejected", "failed"]})
    if status == "processing":
        flt["processing_status"] = {"$in": list(PROCESSING_STATES)}
    elif status == "indexed":
        flt["processing_status"] = "indexed"
    elif status in ("failed", "attention"):
        flt["processing_status"] = {"$in": ["failed", "rejected"]}
    if kind != "all":
        flt["kind"] = kind
    if q:
        flt["$or"] = [
            {"display_title": {"$regex": re.escape(q), "$options": "i"}},
            {"original_filename": {"$regex": re.escape(q), "$options": "i"}},
        ]
    if collection_id and (cid := maybe_oid(collection_id)):
        flt["collection_ids"] = cid
    order = {
        "newest": [("created_at", -1)],
        "oldest": [("created_at", 1)],
        "title": [("display_title", 1)],
        "size": [("original_size_bytes", -1)],
    }[sort]
    db = get_db()
    total = await db.files.count_documents(flt)
    rows = [f async for f in db.files.find(flt).sort(order).skip(skip).limit(page_size)]
    job_ids = [f["ingest_job_id"] for f in rows if f.get("ingest_job_id")]
    jobs = {j["_id"]: j async for j in db.jobs.find({"_id": {"$in": job_ids}}, projection={"status": 1, "progress": 1})}
    return {"items": [file_out(f, jobs.get(f.get("ingest_job_id"))) for f in rows], "total": total, "page": page, "page_size": page_size}


async def _file(ws: Any, file_id: str) -> dict[str, Any]:
    f = await get_db().files.find_one(ws.scope(_id=oid(file_id, what="file"), storage_status={"$ne": "deleted"}))
    if not f:
        raise NotFound("File not found.")
    return f


@router.get("/files/{file_id}")
async def get_file(file_id: str, ws: Workspace) -> dict[str, Any]:
    f = await _file(ws, file_id)
    job = await get_db().jobs.find_one({"_id": f.get("ingest_job_id")}) if f.get("ingest_job_id") else None
    out = file_out(f, job)
    if f.get("source_id"):
        coll = get_db().books if f.get("kind") == "book" else get_db().documents
        src = await coll.find_one(ws.scope(_id=f["source_id"]))
        if src:
            out["source"] = source_out(src, f.get("kind"))
    out["job"] = {"status": job["status"], "attempts": job.get("attempts"), "error": job.get("error")} if job else None
    return out


@router.delete("/files/{file_id}", status_code=202)
async def delete_file(file_id: str, ws: Workspace) -> dict[str, Any]:
    if not ws.can_write:
        raise ValidationFailed("You have read-only access to this workspace.", status_code=403, code="forbidden")
    f = await _file(ws, file_id)
    job_id = await request_file_deletion(f["_id"], ws.workspace_id, ws.user_id)
    return {"status": "deleting", "job_id": str(job_id)}


@router.post("/files/{file_id}/retry")
async def retry_processing(file_id: str, ws: Workspace) -> dict[str, Any]:
    f = await _file(ws, file_id)
    if f["processing_status"] != "failed" or f["storage_status"] != "stored":
        raise ValidationFailed("Only files that failed processing can be retried.")
    job_id = f.get("ingest_job_id")
    if job_id and await queue.retry_failed(job_id):
        await get_db().files.update_one(
            {"_id": f["_id"]}, {"$set": {"processing_status": "queued", "processing_error": None, "updated_at": utcnow()}}
        )
        return {"status": "queued", "job_id": str(job_id)}
    raise ValidationFailed("This file's processing job can't be retried.")


@router.get("/files/{file_id}/download")
async def download(file_id: str, ws: Workspace) -> Any:
    from app.storage import storage_for

    f = await _file(ws, file_id)
    if f["storage_status"] != "stored":
        raise NotFound("File not available.")
    if not original_available(f):
        raise NotFound(
            "This server doesn't keep original files after indexing them. The text stays searchable in your library.",
            code="original_not_kept",
        )
    storage = storage_for(f)
    url = await storage.signed_download_url(
        f["storage_key"], filename=f["original_filename"], ttl_seconds=get_settings().signed_url_ttl_seconds
    )
    if url:
        return RedirectResponse(url, status_code=302)
    safe = "".join(c for c in f["original_filename"] if c.isalnum() or c in " ._-")[:120] or "download"
    return StreamingResponse(
        storage.open_read(f["storage_key"]),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


# --- Sources (books & documents) -----------------------------------------------------------


def source_out(s: dict[str, Any], kind: str | None = None) -> dict[str, Any]:
    return {
        "id": str(s["_id"]),
        "kind": kind,
        "file_id": str(s["file_id"]),
        "title": s.get("title"),
        "author": s.get("author"),
        "publisher": s.get("publisher"),
        "publication_date": s.get("publication_date"),
        "language": s.get("language"),
        "page_count": s.get("page_count"),
        "file_type": s.get("file_type"),
        "status": s.get("status"),
        "chunk_count": s.get("chunk_count", 0),
        "metadata": s.get("metadata"),
        "outline": s.get("outline", [])[:500],
        "warnings": s.get("parse_warnings", []),
        "license_note": s.get("license_note"),
        "created_at": iso(s.get("created_at")),
    }


async def _list_sources(
    coll_name: str, ws: Any, page: int, page_size: int, q: str | None, author: str | None, language: str | None
) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt: dict[str, Any] = ws.scope()
    if q:
        flt["title"] = {"$regex": re.escape(q), "$options": "i"}
    if author:
        flt["author"] = {"$regex": re.escape(author), "$options": "i"}
    if language:
        flt["language"] = language
    coll = get_db()[coll_name]
    total = await coll.count_documents(flt)
    kind = "book" if coll_name == "books" else "document"
    items = [
        source_out(s, kind) async for s in coll.find(flt, projection={"outline": 0}).sort("created_at", -1).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/books")
async def list_books(
    ws: Workspace, page: int = 1, page_size: int = 20, q: str | None = None, author: str | None = None, language: str | None = None
) -> dict[str, Any]:
    return await _list_sources("books", ws, page, page_size, q, author, language)


@router.get("/documents")
async def list_documents(
    ws: Workspace, page: int = 1, page_size: int = 20, q: str | None = None, author: str | None = None, language: str | None = None
) -> dict[str, Any]:
    return await _list_sources("documents", ws, page, page_size, q, author, language)


@router.get("/sources/{source_id}")
async def get_source(source_id: str, ws: Workspace) -> dict[str, Any]:
    sid_ = oid(source_id, what="source")
    for name, kind in (("books", "book"), ("documents", "document")):
        s = await get_db()[name].find_one(ws.scope(_id=sid_))
        if s:
            return source_out(s, kind)
    raise NotFound("Source not found.")


class SourceUpdate(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    author: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    publication_date: str | None = Field(default=None, max_length=40)
    license_note: str | None = Field(default=None, max_length=1000)


@router.patch("/sources/{source_id}")
async def update_source(source_id: str, body: SourceUpdate, ws: Workspace) -> dict[str, Any]:
    sid_ = oid(source_id, what="source")
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not upd:
        raise ValidationFailed("Nothing to update.")
    prov = {f"metadata.{k}": {"value": v, "provenance": "user"} for k, v in upd.items() if k != "license_note"}
    for name, kind in (("books", "book"), ("documents", "document")):
        res = await get_db()[name].find_one_and_update(
            ws.scope(_id=sid_), {"$set": {**upd, **prov, "updated_at": utcnow()}}, return_document=True
        )
        if res:
            if "title" in upd:
                await get_db().chunks.update_many({"source_id": sid_}, {"$set": {"source_title": upd["title"]}})
                await get_db().files.update_one({"_id": res["file_id"]}, {"$set": {"display_title": upd["title"], "title_source": "user"}})
            return source_out(res, kind)
    raise NotFound("Source not found.")


@router.get("/chunks/{chunk_id}")
async def get_chunk(chunk_id: str, ws: Workspace, context: int = Query(0, ge=0, le=2)) -> dict[str, Any]:
    """Source preview for citations, with optional neighbouring chunks."""
    db = get_db()
    c = await db.chunks.find_one(ws.scope(_id=oid(chunk_id, what="passage")), projection={get_settings().vector_field: 0})
    if not c:
        raise NotFound("Passage not found.")
    neighbours = []
    if context:
        neighbours = [
            {
                "id": str(n["_id"]),
                "ordinal": n["ordinal"],
                "text": n["text"][n.get("overlap_prefix_chars", 0) :],
                "page_start": n.get("page_start"),
            }
            async for n in db.chunks.find(
                ws.scope(
                    file_id=c["file_id"], ordinal={"$gte": c["ordinal"] - context, "$lte": c["ordinal"] + context, "$ne": c["ordinal"]}
                ),
                projection={"text": 1, "ordinal": 1, "page_start": 1, "overlap_prefix_chars": 1},
            ).sort("ordinal", 1)
        ]
    return {
        "id": str(c["_id"]),
        "source_id": str(c["source_id"]),
        "source_type": c.get("source_type"),
        "source_title": c.get("source_title"),
        "file_id": str(c["file_id"]),
        "section_path": c.get("section_path", []),
        "page_start": c.get("page_start"),
        "page_end": c.get("page_end"),
        "locations": c.get("locations", []),
        "citation_label": c.get("citation_label"),
        "ordinal": c["ordinal"],
        "text": c["text"],
        "overlap_prefix_chars": c.get("overlap_prefix_chars", 0),
        "neighbours": neighbours,
    }


# --- Collections ---------------------------------------------------------------------------


class CollectionIn(APIModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class CollectionMembership(APIModel):
    file_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["add", "remove"]


@router.get("/collections")
async def list_collections(ws: Workspace) -> dict[str, Any]:
    db = get_db()
    out = []
    async for c in db.collections.find(ws.scope()).sort("name", 1).limit(500):
        n = await db.files.count_documents(ws.scope(collection_ids=c["_id"], storage_status="stored"))
        out.append({"id": str(c["_id"]), "name": c["name"], "description": c.get("description", ""), "file_count": n})
    return {"items": out}


@router.post("/collections", status_code=201)
async def create_collection(body: CollectionIn, ws: Workspace) -> dict[str, Any]:
    from pymongo.errors import DuplicateKeyError

    from app.core.errors import Conflict

    try:
        res = await get_db().collections.insert_one(
            {**ws.scope(), "name": body.name, "description": body.description, "created_by": ws.user_id, "created_at": utcnow()}
        )
    except DuplicateKeyError as exc:
        raise Conflict("A collection with this name already exists.") from exc
    return {"id": str(res.inserted_id), "name": body.name, "description": body.description, "file_count": 0}


@router.patch("/collections/{collection_id}")
async def rename_collection(collection_id: str, body: CollectionIn, ws: Workspace) -> dict[str, Any]:
    res = await get_db().collections.update_one(
        ws.scope(_id=oid(collection_id, what="collection")), {"$set": {"name": body.name, "description": body.description}}
    )
    if not res.matched_count:
        raise NotFound("Collection not found.")
    return {"status": "updated"}


@router.delete("/collections/{collection_id}")
async def delete_collection(collection_id: str, ws: Workspace) -> dict[str, Any]:
    cid = oid(collection_id, what="collection")
    res = await get_db().collections.delete_one(ws.scope(_id=cid))
    if not res.deleted_count:
        raise NotFound("Collection not found.")
    for name in ("files", "books", "documents"):
        await get_db()[name].update_many(ws.scope(collection_ids=cid), {"$pull": {"collection_ids": cid}})
    return {"status": "deleted"}


@router.post("/collections/{collection_id}/files")
async def collection_files(collection_id: str, body: CollectionMembership, ws: Workspace) -> dict[str, Any]:
    cid = oid(collection_id, what="collection")
    if not await get_db().collections.find_one(ws.scope(_id=cid)):
        raise NotFound("Collection not found.")
    ids = [i for i in (maybe_oid(x) for x in body.file_ids) if i]
    op = {"$addToSet": {"collection_ids": cid}} if body.action == "add" else {"$pull": {"collection_ids": cid}}
    res = await get_db().files.update_many(ws.scope(_id={"$in": ids}), op)
    await get_db().books.update_many(ws.scope(file_id={"$in": ids}), op)
    await get_db().documents.update_many(ws.scope(file_id={"$in": ids}), op)
    return {"updated": res.modified_count}


# --- Library metrics -----------------------------------------------------------------------


@router.get("/library/stats")
async def library_stats(ws: Workspace) -> dict[str, Any]:
    db = get_db()
    scope = ws.scope()
    by_status: dict[str, int] = {}
    async for row in await db.files.aggregate(
        [
            {"$match": {**scope, "storage_status": {"$in": ["uploading", "stored", "rejected", "failed"]}}},
            {"$group": {"_id": "$processing_status", "n": {"$sum": 1}}},
        ]
    ):
        by_status[row["_id"] or "unknown"] = row["n"]
    uploaded = await db.files.count_documents({**scope, "storage_status": "stored"})
    return {
        "files_uploaded": uploaded,
        "books": await db.books.count_documents(scope),
        "documents": await db.documents.count_documents(scope),
        "files_processing": sum(by_status.get(s, 0) for s in PROCESSING_STATES),
        "files_need_attention": by_status.get("failed", 0) + by_status.get("rejected", 0),
        "files_indexed": by_status.get("indexed", 0),
        "failed_uploads": await db.files.count_documents({**scope, "storage_status": {"$in": ["rejected", "failed"]}}),
        "indexed_chunks": await db.chunks.count_documents({**scope, active_marker(): get_settings().embedding_space_id}),
        "entities": await db.entities.count_documents(scope),
        "relationships": await db.relationships.count_documents(scope),
        "reports": await db.reports.count_documents({**scope, "user_id": ws.user_id}),
        "conversations": await db.conversations.count_documents({**scope, "user_id": ws.user_id}),
    }


@router.get("/library/activity")
async def library_activity(ws: Workspace, days: int = Query(30, ge=7, le=365)) -> dict[str, Any]:
    from datetime import timedelta

    db = get_db()
    since = utcnow() - timedelta(days=days)
    fmt = {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}
    out: dict[str, dict[str, int]] = {}

    async def series(coll: str, extra: dict[str, Any], key: str) -> None:
        async for row in await db[coll].aggregate(
            [{"$match": {**ws.scope(), **extra, "created_at": {"$gte": since}}}, {"$group": {"_id": fmt, "n": {"$sum": 1}}}]
        ):
            out.setdefault(row["_id"], {})[key] = row["n"]

    await series("files", {"storage_status": "stored"}, "uploads")
    await series("messages", {"role": "user", "user_id": ws.user_id}, "questions")
    await series("reports", {"user_id": ws.user_id}, "reports")
    days_list = [(since + timedelta(days=i + 1)).strftime("%Y-%m-%d") for i in range(days)]
    return {"days": [{"date": d, **{k: out.get(d, {}).get(k, 0) for k in ("uploads", "questions", "reports")}} for d in days_list]}


# --- Jobs ----------------------------------------------------------------------------------


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, ws: Workspace) -> dict[str, Any]:
    j = await get_db().jobs.find_one({"_id": oid(job_id, what="job"), "workspace_id": ws.workspace_id})
    if not j:
        raise NotFound("Job not found.")
    return {
        "id": str(j["_id"]),
        "type": j["type"],
        "status": j["status"],
        "progress": j.get("progress"),
        "attempts": j.get("attempts"),
        "error": {k: j["error"].get(k) for k in ("code", "message", "retryable")} if j.get("error") else None,
        "created_at": iso(j.get("created_at")),
        "finished_at": iso(j.get("finished_at")),
    }
