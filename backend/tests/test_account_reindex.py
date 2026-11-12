from __future__ import annotations

import io
import zipfile

from bson import ObjectId

from app.database.client import mongo
from app.embeddings import get_embedding_provider, set_embedding_provider
from tests import samples
from tests.fakes import FakeEmbeddings, drain
from tests.test_storage_uploads import upload


async def test_data_export_contains_user_data(user):
    await upload(user, "notes.txt", samples.BOOK_TEXT.encode())
    await drain()
    r = await user.post("/api/v1/me/export")
    assert r.status_code == 200
    await drain()
    items = (await user.get("/api/v1/me/exports")).json()["items"]
    assert len(items) == 1
    d = await user.get(f"/api/v1/me/exports/{items[0]['id']}/download")
    assert d.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(d.content))
    names = z.namelist()
    assert "account.json" in names and any(n.endswith("originals/" + n.split("/")[-1]) and "notes.txt" in n for n in names)
    assert b"password_hash" not in z.read("account.json")


async def test_account_deletion_removes_content_and_sessions(user, other_user):
    fid = (await upload(user, "mine.txt", samples.BOOK_TEXT.encode())).json()["file"]["id"]
    await drain()
    me = (await user.get("/api/v1/me")).json()
    r = await user.delete("/api/v1/me", json={"confirm_email": "wrong@example.com"})
    assert r.status_code == 422
    r = await user.delete("/api/v1/me", json={"confirm_email": me["email"]})
    assert r.status_code == 200
    await drain()
    uid = ObjectId(me["id"])
    assert await mongo.db.chunks.count_documents({"owner_id": uid}) == 0
    assert await mongo.db.entities.count_documents({}) == 0
    assert await mongo.db.sessions.count_documents({"user_id": uid}) == 0
    f = await mongo.db.files.find_one({"_id": ObjectId(fid)})
    assert f["storage_status"] == "deleted" and f["original_filename"] == "[deleted]"
    u = await mongo.db.users.find_one({"_id": uid})
    assert u["email"].startswith("deleted-") and u["password_hash"] is None
    assert (await user.get("/api/v1/me")).status_code == 401
    # Other users are unaffected.
    assert (await other_user.get("/api/v1/me")).status_code == 200


async def test_reindex_migration_never_mixes_spaces(user, super_admin):
    await upload(user, "a.txt", samples.BOOK_TEXT.encode())
    await drain()
    total = await mongo.db.chunks.count_documents({})
    original = get_embedding_provider()
    r = await super_admin.post(
        "/api/v1/admin/reindex",
        json={"model": "gemini-embedding-2", "dimensions": 256, "vector_field": "embedding_v2", "note": "bigger vectors"},
    )
    assert r.status_code == 202, r.text
    # The target provider for a different space is built from settings; substitute a fake with the target dims.
    import app.retrieval.reindex as reindex

    saved = reindex.provider_for
    reindex.provider_for = lambda target: FakeEmbeddings(dims=target["dimensions"], space=target["space_id"])  # type: ignore[assignment]
    try:
        await drain()
    finally:
        reindex.provider_for = saved
        set_embedding_provider(original)
    chunk = await mongo.db.chunks.find_one({})
    assert len(chunk["embedding"]) == 128 and len(chunk["embedding_v2"]) == 256
    assert chunk["embedding_space"] != chunk["embedding_v2_space"]
    lst = (await super_admin.get("/api/v1/admin/reindex")).json()["items"][0]
    assert lst["complete"] and lst["embedded"] == total and lst["status"] == "completed"
    # The active retrieval path still filters on the active space only.
    hits = (await user.post("/api/v1/search", json={"query": "waggle dance"})).json()["items"]
    assert hits
    bad = await super_admin.post(
        "/api/v1/admin/reindex", json={"model": "x-model", "dimensions": 128, "vector_field": "embedding", "note": "same field"}
    )
    assert bad.status_code == 422


def test_eval_metrics():
    from app.retrieval.search import Hit
    from scripts.eval_retrieval import metrics

    def h(title, text):
        return Hit(
            id=ObjectId(),
            text=text,
            file_id=ObjectId(),
            source_id=ObjectId(),
            source_type="book",
            source_title=title,
            section_path=[],
            page_start=None,
            page_end=None,
            citation_label="",
            ordinal=0,
        )

    exp = [{"source_title": "Bees", "contains": "waggle"}]
    res = [([h("Other", "x"), h("Bees", "the waggle dance")], exp), ([h("Bees", "nothing")], exp)]
    m = metrics(res, 8)
    assert m["recall@8"] == 0.5 and m["mrr"] == 0.25 and m["questions"] == 2
