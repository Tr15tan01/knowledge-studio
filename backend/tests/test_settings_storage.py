"""Admin runtime settings, the MongoDB storage backend, and originals deleted after indexing."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from bson import ObjectId

from app.billing.entitlements import resolve
from app.billing.storage_accounting import reconcile_storage_objects
from app.core.timeutil import utcnow
from app.database.client import mongo
from app.documents.originals import purge_due_originals
from app.jobs import queue
from app.retrieval.budget import estimate_question
from app.services.system_settings import get_runtime_settings, runtime_defaults, save_runtime_settings
from app.storage import storage_backend
from app.storage.base import UploadTooLarge
from tests import samples
from tests.fakes import drain
from tests.test_storage_uploads import account_usage, upload


async def configure(**changes: Any) -> None:
    await save_runtime_settings(runtime_defaults().model_copy(update=changes), None)


async def stream(*parts: bytes):
    for p in parts:
        yield p


# --- MongoDB storage backend ----------------------------------------------------------------


async def test_mongo_storage_round_trip(app):
    st = storage_backend("mongodb")
    stored = await st.put_stream("files/ws/abc", stream(b"hello ", b"", b"world"), max_bytes=100, content_type="text/plain")
    assert stored.size_bytes == 11 and stored.head == b"hello world" and len(stored.sha256) == 64
    assert await st.exists("files/ws/abc") and await st.size("files/ws/abc") == 11
    assert await st.read_bytes("files/ws/abc", max_bytes=11) == b"hello world"
    assert b"".join([b async for b in st.open_read("files/ws/abc")]) == b"hello world"
    assert [(k, n) async for k, n, _ in st.list_keys("files/")] == [("files/ws/abc", 11)]
    assert [k async for k, _, _ in st.list_keys("exports/")] == []
    with pytest.raises(UploadTooLarge):
        await st.read_bytes("files/ws/abc", max_bytes=10)
    # Writing a key again replaces the object.
    await st.put_stream("files/ws/abc", stream(b"v2"), max_bytes=100, content_type="text/plain")
    assert await st.read_bytes("files/ws/abc", max_bytes=100) == b"v2"
    assert await st.delete("files/ws/abc") and not await st.exists("files/ws/abc")
    assert await st.delete("files/ws/abc")  # idempotent
    with pytest.raises(FileNotFoundError):
        await st.read_bytes("files/ws/abc", max_bytes=100)
    assert (await st.health())[0]


async def test_mongo_storage_aborts_oversized_upload(app):
    st = storage_backend("mongodb")
    with pytest.raises(UploadTooLarge):
        await st.put_stream("files/ws/big", stream(b"x" * 600_000, b"y" * 600_000), max_bytes=1_000_000, content_type="x")
    assert not await st.exists("files/ws/big")
    assert await mongo.db["storage.chunks"].count_documents({}) == 0  # no partial data left behind


# --- Originals after indexing ---------------------------------------------------------------


async def test_original_is_deleted_after_indexing_by_default(user):
    await configure(keep_original_files=False)
    data = samples.BOOK_TEXT.encode()
    fid = (await upload(user, "travels.txt", data, kind="book")).json()["file"]["id"]
    assert ("ingest.file", "completed") in await drain()
    f = (await user.get(f"/api/v1/files/{fid}")).json()
    assert f["processing_status"] == "indexed" and f["storage_status"] == "stored" and f["original_available"] is False
    doc = await mongo.db.files.find_one({"_id": ObjectId(fid)})
    assert doc["original_purged_at"] and not await storage_backend("local").exists(doc["storage_key"])
    # The passages (and so answers and citations) are still there.
    assert await mongo.db.chunks.count_documents({"file_id": ObjectId(fid), "embedding": {"$ne": None}}) == f["chunk_count"] >= 3
    r = await user.get(f"/api/v1/files/{fid}/download")
    assert r.status_code == 404 and r.json()["error"]["code"] == "original_not_kept"
    # The library entry still counts toward the storage quota, and nothing is reported missing.
    assert (await account_usage(user))["storage"]["used_bytes"] == len(data)
    report = await reconcile_storage_objects(orphan_grace=timedelta(seconds=0))
    assert report["missing_objects"] == 0 and report["orphans_deleted"] == 0
    # The same file is still recognised as a duplicate.
    assert (await upload(user, "again.txt", data)).status_code == 409
    # Deleting the entry releases the quota.
    assert (await user.delete(f"/api/v1/files/{fid}")).status_code == 202
    await drain()
    assert (await account_usage(user))["storage"]["used_bytes"] == 0


async def test_keeping_originals_is_a_setting(user):
    await configure(keep_original_files=True)
    data = samples.BOOK_TEXT.encode()
    fid = (await upload(user, "kept.txt", data)).json()["file"]["id"]
    await drain()
    f = (await user.get(f"/api/v1/files/{fid}")).json()
    assert f["original_available"] is True
    assert (await user.get(f"/api/v1/files/{fid}/download")).content == data


async def test_export_skips_deleted_originals(user):
    import io
    import zipfile

    await configure(keep_original_files=False)
    await upload(user, "gone.txt", samples.BOOK_TEXT.encode())
    await drain()
    assert (await user.post("/api/v1/me/export")).status_code in (200, 202)
    assert ("account.export", "completed") in await drain()
    items = (await user.get("/api/v1/me/exports")).json()["items"]
    d = await user.get(f"/api/v1/me/exports/{items[0]['id']}/download")
    names = zipfile.ZipFile(io.BytesIO(d.content)).namelist()
    assert any(n.endswith("files.json") for n in names) and not any("/originals/" in n for n in names)


async def test_purged_file_is_re_embedded_from_stored_passages(user):
    await configure(keep_original_files=False)
    fid = ObjectId((await upload(user, "b.txt", samples.BOOK_TEXT.encode())).json()["file"]["id"])
    await drain()
    n = await mongo.db.chunks.count_documents({"file_id": fid})
    # Simulate a new embedding space: every vector is stale.
    await mongo.db.files.update_one({"_id": fid}, {"$set": {"embedding_space": "old:space:1"}})
    await mongo.db.chunks.update_many({"file_id": fid}, {"$set": {"embedding_space": "old:space:1", "embedding": None}})
    await queue.enqueue("ingest.file", {"file_id": str(fid)}, dedupe_key=f"reprocess:{fid}")
    assert ("ingest.file", "completed") in await drain(types=["ingest.file"])
    f = await mongo.db.files.find_one({"_id": fid})
    assert f["processing_status"] == "indexed" and f["embedding_space"] != "old:space:1" and f["chunk_count"] == n
    assert await mongo.db.chunks.count_documents({"file_id": fid, "embedding_space": f["embedding_space"], "embedding": {"$ne": None}}) == n


async def test_purged_file_without_passages_needs_a_new_upload(user):
    await configure(keep_original_files=False)
    fid = ObjectId((await upload(user, "c.txt", samples.BOOK_TEXT.encode())).json()["file"]["id"])
    await drain()
    await mongo.db.chunks.delete_many({"file_id": fid})
    await mongo.db.files.update_one({"_id": fid}, {"$set": {"chunking_version": "older"}})
    await queue.enqueue("ingest.file", {"file_id": str(fid)}, dedupe_key=f"reprocess:{fid}", max_attempts=1)
    await drain(types=["ingest.file"])
    f = await mongo.db.files.find_one({"_id": fid})
    assert f["processing_status"] == "failed" and f["processing_error"]["code"] == "original_not_kept"


async def test_maintenance_drops_originals_of_old_failed_files(user):
    from app.core.errors import ProviderError
    from app.embeddings import get_embedding_provider

    await configure(keep_original_files=False)
    get_embedding_provider().fail_next = ProviderError("bad key", code="provider_auth_failed", status_code=503)
    fid = ObjectId((await upload(user, "f.txt", b"text that fails to embed")).json()["file"]["id"])
    await drain()
    f = await mongo.db.files.find_one({"_id": fid})
    local = storage_backend("local")
    assert f["processing_status"] == "failed" and await local.exists(f["storage_key"])  # kept so a retry can work
    assert await purge_due_originals() == 0
    await mongo.db.files.update_one({"_id": fid}, {"$set": {"updated_at": utcnow() - timedelta(days=8)}})
    assert await purge_due_originals() == 1
    assert not await local.exists(f["storage_key"])
    assert (await mongo.db.files.find_one({"_id": fid}))["original_purged_at"]


# --- Choosing where files are stored --------------------------------------------------------


async def test_admin_chooses_mongodb_storage(user):
    await configure(storage_backend="mongodb", keep_original_files=True)
    data = samples.BOOK_TEXT.encode()
    fid = (await upload(user, "m.txt", data)).json()["file"]["id"]
    doc = await mongo.db.files.find_one({"_id": ObjectId(fid)})
    assert doc["storage_backend"] == "mongodb"
    assert await storage_backend("mongodb").exists(doc["storage_key"]) and not await storage_backend("local").exists(doc["storage_key"])
    assert ("ingest.file", "completed") in await drain()
    assert (await user.get(f"/api/v1/files/{fid}/download")).content == data
    await user.delete(f"/api/v1/files/{fid}")
    await drain()
    assert not await storage_backend("mongodb").exists(doc["storage_key"])


async def test_mongodb_storage_holds_the_file_only_until_indexed(user):
    await configure(storage_backend="mongodb", keep_original_files=False)
    fid = (await upload(user, "m.txt", samples.BOOK_TEXT.encode())).json()["file"]["id"]
    assert await mongo.db["storage.files"].count_documents({}) == 1
    await drain()
    assert (await user.get(f"/api/v1/files/{fid}")).json()["processing_status"] == "indexed"
    assert await mongo.db["storage.files"].count_documents({}) == 0 and await mongo.db["storage.chunks"].count_documents({}) == 0


async def test_files_stay_readable_after_the_backend_changes(user):
    await configure(storage_backend="mongodb", keep_original_files=True)
    data = samples.BOOK_TEXT.encode()
    fid = (await upload(user, "first.txt", data)).json()["file"]["id"]
    await configure(storage_backend="local", keep_original_files=True)
    assert ("ingest.file", "completed") in await drain()  # read from where it was written
    assert (await user.get(f"/api/v1/files/{fid}/download")).content == data
    other = (await upload(user, "second.txt", b"a second, different document")).json()["file"]["id"]
    assert (await mongo.db.files.find_one({"_id": ObjectId(other)}))["storage_backend"] == "local"
    # Orphans are found in every backend.
    await storage_backend("mongodb").put_stream("files/orphan/x", stream(b"orphan"), max_bytes=100, content_type="x")
    report = await reconcile_storage_objects(orphan_grace=timedelta(seconds=0))
    assert report["orphans_deleted"] == 1 and report["missing_objects"] == 0


# --- Admin settings API ---------------------------------------------------------------------


def settings_body(current: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**current, **changes, "reason": "tuning"}


async def test_settings_roles_validation_and_audit(admin, super_admin, user):
    assert (await user.get("/api/v1/admin/settings")).status_code == 403
    r = await admin.get("/api/v1/admin/settings")
    assert r.status_code == 200
    out = r.json()
    cur = out["settings"]
    assert out["storage_backends"] == ["local", "mongodb"]  # S3 is offered only when configured
    assert cur == out["defaults"] and cur["storage_backend"] == "local"
    # Admins can look; only super admins can change.
    assert (await admin.put("/api/v1/admin/settings", json=settings_body(cur, retrieval_top_k=5))).status_code == 403
    for bad in (
        {"retrieval_top_k": 30, "retrieval_candidates": 10},
        {"context_max_tokens": 10},
        {"storage_backend": "s3"},
        {"storage_backend": "ftp"},
        {"unknown_setting": 1},
    ):
        assert (await super_admin.put("/api/v1/admin/settings", json=settings_body(cur, **bad))).status_code == 422, bad
    assert (await super_admin.put("/api/v1/admin/settings", json={**cur, "retrieval_top_k": 5})).status_code == 422  # reason required
    r = await super_admin.put(
        "/api/v1/admin/settings", json=settings_body(cur, storage_backend="mongodb", context_max_tokens=4000, retrieval_top_k=5)
    )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["settings"]["storage_backend"] == "mongodb" and saved["updated_by"] == super_admin.user["id"]
    assert saved["defaults"]["storage_backend"] == "local"
    ev = await mongo.db.audit_events.find_one({"action": "admin.settings_changed"})
    assert ev["reason"] == "tuning" and ev["details"]["changes"] == [
        {"setting": "context_max_tokens", "before": cur["context_max_tokens"], "after": 4000},
        {"setting": "retrieval_top_k", "before": cur["retrieval_top_k"], "after": 5},
        {"setting": "storage_backend", "before": "local", "after": "mongodb"},
    ]
    # The new values are what the application runs on.
    rt = await get_runtime_settings()
    assert rt.storage_backend == "mongodb" and rt.retrieval_top_k == 5
    est = await estimate_question(await resolve(ObjectId(user.user["id"])), "standard_question")
    assert est.input_tokens == 4000 + 1500
    fid = (await upload(user, "s.txt", b"stored where the admin said")).json()["file"]["id"]
    assert (await mongo.db.files.find_one({"_id": ObjectId(fid)}))["storage_backend"] == "mongodb"
    held = (await admin.get("/api/v1/admin/settings")).json()["files_held"]
    assert held == [{"backend": "mongodb", "files": 1, "bytes": len(b"stored where the admin said")}]


async def test_settings_change_requires_recent_auth(super_admin):
    cur = (await super_admin.get("/api/v1/admin/settings")).json()["settings"]
    await mongo.db.sessions.update_many(
        {"user_id": ObjectId(super_admin.user["id"])}, {"$set": {"reauth_at": utcnow() - timedelta(hours=2)}}
    )
    r = await super_admin.put("/api/v1/admin/settings", json=settings_body(cur, retrieval_top_k=5))
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauth_required"


async def test_sign_ups_can_be_closed(app, client):
    assert (await client.get("/api/v1/auth/config")).json()["registration_enabled"] is True
    await configure(registration_enabled=False)
    assert (await client.get("/api/v1/auth/config")).json()["registration_enabled"] is False
    r = await client.post("/api/v1/auth/register", json={"email": "late@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "registration_closed"
    assert await mongo.db.users.count_documents({"email_normalized": "late@example.com"}) == 0


async def test_graph_extraction_can_be_switched_off(user):
    await configure(graph_extraction_enabled=False)
    await upload(user, "g.txt", samples.BOOK_TEXT.encode())
    assert "graph.extract" not in [t for t, _ in await drain()]
