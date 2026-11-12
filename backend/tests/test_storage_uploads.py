from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from bson import ObjectId

from app.billing import usage
from app.billing.entitlements import resolve
from app.billing.periods import LIFETIME
from app.billing.storage_accounting import reconcile_account_storage, reconcile_storage_objects, storage_breakdown
from app.core.timeutil import utcnow
from app.core.units import KIB
from app.database.client import mongo
from tests import samples
from tests.fakes import drain


async def upload(session, name: str, data: bytes, *, kind: str = "auto", key: str | None = None):
    headers = {"x-filename": name, "content-type": "application/octet-stream"}
    if key:
        headers["idempotency-key"] = key
    return await session.post(f"/api/v1/files/upload?kind={kind}", content=data, headers=headers)


async def set_free_limits(**limits: Any) -> None:
    plan = await mongo.db.plans.find_one({"slug": "free"})
    await mongo.db.plan_versions.update_one(
        {"plan_id": plan["_id"], "version": plan["current_version"]}, {"$set": {f"entitlements.{k}": v for k, v in limits.items()}}
    )


async def account_usage(session) -> dict[str, Any]:
    return (await session.get("/api/v1/me/usage")).json()


async def test_upload_ingest_index_and_counts(user):
    data = samples.BOOK_TEXT.encode()
    r = await upload(user, "travels.txt", data, kind="book")
    assert r.status_code == 202, r.text
    fid = r.json()["file"]["id"]
    assert r.json()["file"]["processing_status"] == "queued"
    results = await drain()
    assert ("ingest.file", "completed") in results
    f = (await user.get(f"/api/v1/files/{fid}")).json()
    assert f["processing_status"] == "indexed" and f["kind"] == "book" and f["chunk_count"] >= 3
    assert f["source"]["outline"][0]["title"] == "Chapter 1: The River"
    chunk = await mongo.db.chunks.find_one({"file_id": ObjectId(fid)})
    assert chunk["embedding_space"] and len(chunk["embedding"]) == 128 and chunk["embedding_model"] == "gemini-embedding-2"
    assert chunk["chunking_version"] and chunk["section_path"]
    stats = (await user.get("/api/v1/library/stats")).json()
    assert stats["files_uploaded"] == 1 and stats["books"] == 1 and stats["documents"] == 0
    assert stats["indexed_chunks"] == f["chunk_count"]  # chunks are not counted as documents
    u = await account_usage(user)
    assert u["usage"]["uploads"]["used"] == 1
    assert u["storage"]["used_bytes"] == len(data)
    assert u["storage"]["breakdown"]["book_bytes"] == len(data)
    assert u["storage"]["display"]["limit"] == "500 MiB"


async def test_pdf_and_docx_ingestion(user):
    pdf = samples.pdf_with_outline(
        [("Rivers", ["Rivers carry sediment to the sea. " * 40]), ("Bees", ["Bees dance to share directions. " * 40])]
    )
    assert (await upload(user, "nature.pdf", pdf)).status_code == 202
    assert (await upload(user, "garden.docx", samples.docx_bytes())).status_code == 202
    await drain()
    files = (await user.get("/api/v1/files")).json()["items"]
    assert {f["processing_status"] for f in files} == {"indexed"}
    pdf_chunk = await mongo.db.chunks.find_one({"source_title": "A Test Book", "section_title": "Bees"})
    assert pdf_chunk and pdf_chunk["page_start"] >= 2 and pdf_chunk["citation_label"].startswith("p")


async def test_file_size_limit_enforced_without_consuming_quota(user):
    await set_free_limits(max_file_size_bytes=1 * KIB, storage_limit_bytes=8 * KIB)
    r = await upload(user, "big.txt", b"x" * (2 * KIB))
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"
    u = await account_usage(user)
    assert u["usage"]["uploads"]["used"] == 0 and u["storage"]["used_bytes"] == 0


async def test_total_storage_limit_enforced(user):
    await set_free_limits(max_file_size_bytes=3 * KIB, storage_limit_bytes=5 * KIB)
    assert (await upload(user, "a.txt", b"a " * 1200)).status_code == 202
    r = await upload(user, "b.txt", b"b " * 1500)
    assert r.status_code == 402 and r.json()["error"]["details"]["metric"] == "storage_bytes"
    u = await account_usage(user)
    assert u["usage"]["uploads"] == {**u["usage"]["uploads"], "used": 1, "reserved": 0}
    assert u["storage"]["used_bytes"] == 2400 and u["storage"]["reserved_bytes"] == 0


async def test_concurrent_uploads_cannot_exceed_storage(user):
    await set_free_limits(max_file_size_bytes=3 * KIB, storage_limit_bytes=7 * KIB, upload_limit_per_period=50)
    payloads = [(f"f{i}.txt", (f"unique {i} " * 300).encode()[: 3 * KIB - 10]) for i in range(8)]
    results = await asyncio.gather(*(upload(user, n, d) for n, d in payloads))
    ok = [r for r in results if r.status_code == 202]
    assert len(ok) == 2  # 2 x ~3 KiB fit into 7 KiB, a third does not
    assert all(r.status_code == 402 for r in results if r.status_code != 202)
    u = await account_usage(user)
    assert u["storage"]["used_bytes"] <= 7 * KIB and u["storage"]["reserved_bytes"] == 0
    assert u["usage"]["uploads"]["used"] == 2 and u["usage"]["uploads"]["reserved"] == 0


async def test_upload_count_limit_and_failed_uploads_free(user):
    bad = await upload(user, "fake.pdf", b"this is not a pdf")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "signature_mismatch"
    unsupported = await upload(user, "virus.exe", b"MZ\x00\x00")
    assert unsupported.status_code == 422
    for i in range(5):
        assert (await upload(user, f"n{i}.txt", f"note number {i}".encode())).status_code == 202
    sixth = await upload(user, "n6.txt", b"one too many")
    assert sixth.status_code == 402 and sixth.json()["error"]["details"]["metric"] == "uploads"
    assert sixth.json()["error"]["details"]["reset_at"]
    u = await account_usage(user)
    assert u["usage"]["uploads"]["used"] == 5
    stats = (await user.get("/api/v1/library/stats")).json()
    assert stats["failed_uploads"] == 1 and stats["files_uploaded"] == 5


async def test_duplicate_upload_rejected_and_idempotent_retry(user):
    data = b"identical content for dedupe"
    first = await upload(user, "a.txt", data, key="k-1")
    again = await upload(user, "a.txt", data, key="k-1")  # client retry with the same key
    assert first.status_code == again.status_code == 202
    assert again.json()["replayed"] and again.json()["file"]["id"] == first.json()["file"]["id"]
    dup = await upload(user, "copy.txt", data, key="k-2")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_file"
    u = await account_usage(user)
    assert u["usage"]["uploads"]["used"] == 1 and u["storage"]["used_bytes"] == len(data)


async def test_delete_releases_storage_once_but_not_upload_allowance(user):
    data = samples.BOOK_TEXT.encode()
    fid = (await upload(user, "b.txt", data)).json()["file"]["id"]
    await drain()
    assert await mongo.db.chunks.count_documents({"file_id": ObjectId(fid)}) > 0
    r = await user.delete(f"/api/v1/files/{fid}")
    assert r.status_code == 202
    # Storage is still counted until deletion is confirmed.
    assert (await account_usage(user))["storage"]["used_bytes"] == len(data)
    await drain()
    u = await account_usage(user)
    assert u["storage"]["used_bytes"] == 0
    assert u["usage"]["uploads"]["used"] == 1  # default policy: deletion doesn't restore uploads
    assert await mongo.db.chunks.count_documents({"file_id": ObjectId(fid)}) == 0
    # Re-running the deletion (e.g. a retried job) never double-releases.
    from app.jobs import queue

    await queue.enqueue("file.delete", {"file_id": fid}, dedupe_key=f"delete-again:{fid}")
    await drain()
    f = await mongo.db.files.find_one({"_id": ObjectId(fid)})
    from app.billing.storage_accounting import release_file_storage

    await mongo.db.files.update_one({"_id": f["_id"]}, {"$set": {"storage_status": "deleting"}})
    assert await release_file_storage(f) is False  # ledger idempotency key blocks a second release
    assert (await account_usage(user))["storage"]["used_bytes"] == 0
    assert await mongo.db.usage_events.count_documents({"metric": "storage_bytes", "amount": {"$lt": 0}}) == 1


async def test_upload_restore_policy_configurable(user):
    await mongo.db.system_settings.update_one({"_id": "quota_policy"}, {"$set": {"upload_quota_restore_on_delete": True}}, upsert=True)
    fid = (await upload(user, "r.txt", b"restore me please")).json()["file"]["id"]
    await drain()
    await user.delete(f"/api/v1/files/{fid}")
    await drain()
    assert (await account_usage(user))["usage"]["uploads"]["used"] == 0


async def test_duplicate_references_do_not_double_count(user):
    data = samples.BOOK_TEXT.encode()
    await upload(user, "b.txt", data, kind="book")
    await drain()
    acct = ObjectId(user.user["id"])
    br = await storage_breakdown(acct)
    assert br["stored_bytes"] == len(data) and br["stored_files"] == 1
    assert await mongo.db.books.count_documents({}) == 1


async def test_reconciliation_repairs_ledger_orphans_and_missing(user):
    from app.storage import storage_backend

    data = b"reconcile me " * 50
    fid = (await upload(user, "r.txt", data)).json()["file"]["id"]
    await drain()
    acct = ObjectId(user.user["id"])
    cid = usage.counter_id(acct, "storage_bytes", "lifetime")
    # Corrupt both the counter and the ledger.
    await mongo.db.usage_counters.update_one({"_id": cid}, {"$set": {"used": 1}})
    await mongo.db.usage_events.delete_many({"account_id": acct, "metric": "storage_bytes"})
    await mongo.db.usage_reservations.delete_many({"account_id": acct, "metric": "storage_bytes"})
    res = await reconcile_account_storage(acct)
    assert res["repaired"]
    assert (await usage.usage_snapshot(acct, "storage_bytes", LIFETIME))["used"] == len(data)
    # Orphan object (no file record) older than grace is removed; a stored file with a missing object is flagged.
    storage = storage_backend("local")

    async def gen():
        yield b"orphan"

    await storage.put_stream("files/orphan/abc123", gen(), max_bytes=100, content_type="x")
    f = await mongo.db.files.find_one({"_id": ObjectId(fid)})
    await storage.delete(f["storage_key"])
    report = await reconcile_storage_objects(orphan_grace=timedelta(seconds=0))
    assert report["orphans_deleted"] == 1 and report["missing_objects"] == 1
    assert not await storage.exists("files/orphan/abc123")
    assert (await mongo.db.files.find_one({"_id": ObjectId(fid)}))["storage_missing"] is True


async def test_stale_upload_reservations_released(user):
    acct = ObjectId(user.user["id"])
    r = await resolve(acct)
    from app.billing.entitlements import reserve_metric

    up = await reserve_metric(r, "uploads", 1)
    st = await reserve_metric(r, "storage_bytes", 500)
    await mongo.db.files.insert_one(
        {
            "owner_id": acct,
            "workspace_id": ObjectId(user.user["default_workspace_id"]),
            "storage_key": "files/x/stale1",
            "storage_status": "uploading",
            "original_size_bytes": 0,
            "created_at": utcnow() - timedelta(hours=3),
            "upload_reservation_id": up.id,
            "storage_reservation_id": st.id,
        }
    )
    report = await reconcile_storage_objects()
    assert report["stale_uploads"] == 1
    u = await account_usage(user)
    assert u["usage"]["uploads"]["reserved"] == 0 and u["storage"]["reserved_bytes"] == 0


async def test_downgrade_keeps_files_but_blocks_new_uploads(user):
    data = b"z " * 3000
    await upload(user, "keep.txt", data)
    await drain()
    await set_free_limits(storage_limit_bytes=2 * KIB, max_file_size_bytes=1 * KIB)
    u = await account_usage(user)
    assert u["storage"]["over_limit"] is True and u["storage"]["warning_level"] == 100
    files = (await user.get("/api/v1/files")).json()["items"]
    assert len(files) == 1 and files[0]["storage_status"] == "stored"  # never silently deleted
    r = await upload(user, "new.txt", b"tiny")
    assert r.status_code == 402


async def test_storage_warning_thresholds(user):
    await set_free_limits(storage_limit_bytes=10 * KIB, max_file_size_bytes=9 * KIB)
    await upload(user, "a.txt", b"q" * int(8.5 * KIB))
    u = await account_usage(user)
    assert u["storage"]["warning_level"] == 80 and not u["storage"]["over_limit"]
    assert u["storage"]["percent_used"] == 85.0


async def test_cross_tenant_access_denied(user, other_user):
    fid = (await upload(user, "private.txt", samples.BOOK_TEXT.encode())).json()["file"]["id"]
    await drain()
    chunk = await mongo.db.chunks.find_one({"file_id": ObjectId(fid)})
    for path in (
        f"/api/v1/files/{fid}",
        f"/api/v1/files/{fid}/download",
        f"/api/v1/chunks/{chunk['_id']}",
        f"/api/v1/sources/{chunk['source_id']}",
    ):
        assert (await other_user.get(path)).status_code == 404, path
    assert (await other_user.delete(f"/api/v1/files/{fid}")).status_code == 404
    # Forged workspace header is rejected.
    r = await other_user.get("/api/v1/files", headers={"x-workspace-id": user.user["default_workspace_id"]})
    assert r.status_code == 404
    assert (await other_user.get("/api/v1/files")).json()["total"] == 0
    # Owner can download.
    d = await user.get(f"/api/v1/files/{fid}/download")
    assert d.status_code == 200 and d.content == samples.BOOK_TEXT.encode()


async def test_ingestion_failure_marks_file_failed_and_retry(user):
    from app.core.errors import ProviderError
    from app.embeddings import get_embedding_provider

    fake = get_embedding_provider()
    fake.fail_next = ProviderError("bad key", code="provider_auth_failed", status_code=503)
    fid = (await upload(user, "f.txt", b"some text to embed for failure test")).json()["file"]["id"]
    await drain()
    f = (await user.get(f"/api/v1/files/{fid}")).json()
    assert f["processing_status"] == "failed" and f["processing_error"]["code"] == "provider_auth_failed"
    r = await user.post(f"/api/v1/files/{fid}/retry")
    assert r.status_code == 200
    await drain()
    assert (await user.get(f"/api/v1/files/{fid}")).json()["processing_status"] == "indexed"


async def test_scanned_pdf_reports_ocr_required(user):
    fid = (await upload(user, "scan.pdf", samples.blank_pdf())).json()["file"]["id"]
    await drain()
    f = (await user.get(f"/api/v1/files/{fid}")).json()
    assert f["processing_status"] == "failed" and f["processing_error"]["code"] == "ocr_required"
    assert await mongo.db.chunks.count_documents({"file_id": ObjectId(fid)}) == 0
