from __future__ import annotations

from datetime import timedelta

import pytest
from bson import ObjectId

from app.core.timeutil import utcnow
from app.database.client import mongo
from app.jobs import queue
from tests import samples
from tests.fakes import FAKE_LLM, drain
from tests.test_entitlements import _premium_sub
from tests.test_storage_uploads import upload


@pytest.fixture(autouse=True)
def reset_llm():
    FAKE_LLM.mode = "normal"
    FAKE_LLM.calls.clear()
    yield
    FAKE_LLM.mode = "normal"


async def _library(user) -> str:
    fid = (await upload(user, "georgia.txt", samples.BOOK_TEXT.encode(), kind="book")).json()["file"]["id"]
    await drain()
    return fid


# --- Knowledge graph ------------------------------------------------------------------------


async def test_graph_extraction_with_provenance(user):
    fid = await _library(user)
    f = await mongo.db.files.find_one({"_id": ObjectId(fid)})
    assert f["graph_status"] == "complete"
    meta = (await user.get("/api/v1/graph/meta")).json()
    assert meta["relationships"] >= 1 and "not verified" in meta["disclaimer"]
    ents = (await user.get("/api/v1/graph/entities", params={"q": "Tbilisi"})).json()["items"]
    assert ents and ents[0]["name"] == "Tbilisi"
    detail = (await user.get(f"/api/v1/graph/entities/{ents[0]['id']}")).json()
    assert detail["mentions"] and "Tbilisi" in detail["mentions"][0]["snippet"]
    assert detail["sources"][0]["title"]
    # Every relationship has evidence pointing to a real chunk of this workspace.
    async for rel in mongo.db.relationships.find({}):
        assert rel["evidence"] and rel["provenance"] == "ai_extracted" and rel["verified"] is False
        for ev in rel["evidence"]:
            assert await mongo.db.chunks.find_one({"_id": ev["chunk_id"], "workspace_id": rel["workspace_id"]})
    assert not await mongo.db.entities.find_one({"normalized_name": "ghost"})  # invalid endpoints dropped
    rel = await mongo.db.relationships.find_one({})
    rd = (await user.get(f"/api/v1/graph/relationships/{rel['_id']}")).json()
    assert rd["evidence"] and rd["confidence_is_estimate"] and rd["disclaimer"]


async def test_graph_extraction_idempotent_and_resumable(user):
    fid = await _library(user)
    n_ent = await mongo.db.entities.count_documents({})
    n_men = await mongo.db.entity_mentions.count_documents({})
    calls_before = FAKE_LLM.calls.count("graph_extraction")
    await queue.enqueue("graph.extract", {"file_id": fid}, dedupe_key="graph-again")
    await drain()
    assert await mongo.db.entities.count_documents({}) == n_ent
    assert await mongo.db.entity_mentions.count_documents({}) == n_men
    assert FAKE_LLM.calls.count("graph_extraction") == calls_before  # all chunks already stamped


async def test_graph_neighborhood_bounded_and_gated(user):
    await _library(user)
    ent = await mongo.db.entities.find_one({"name": "Tbilisi"})
    r = await user.get(f"/api/v1/graph/neighborhood/{ent['_id']}", params={"depth": 1, "limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert any(n["id"] == str(ent["_id"]) for n in body["nodes"]) and body["limits"]["max_nodes"] == 10
    assert all(e["source"] in {n["id"] for n in body["nodes"]} for e in body["edges"])
    # Depth 2 is an advanced (Premium) graph feature.
    r2 = await user.get(f"/api/v1/graph/neighborhood/{ent['_id']}", params={"depth": 2})
    assert r2.status_code == 403
    ov = (await user.get("/api/v1/graph/overview", params={"limit": 10})).json()
    assert len(ov["nodes"]) <= 20


async def test_graph_cross_tenant_isolation(user, other_user):
    await _library(user)
    ent = await mongo.db.entities.find_one({})
    rel = await mongo.db.relationships.find_one({})
    assert (await other_user.get(f"/api/v1/graph/entities/{ent['_id']}")).status_code == 404
    assert (await other_user.get(f"/api/v1/graph/relationships/{rel['_id']}")).status_code == 404
    assert (await other_user.get(f"/api/v1/graph/neighborhood/{ent['_id']}")).status_code == 404
    assert (await other_user.get("/api/v1/graph/entities")).json()["total"] == 0


async def test_file_deletion_removes_graph_provenance(user):
    fid = await _library(user)
    assert await mongo.db.entities.count_documents({}) > 0
    await user.delete(f"/api/v1/files/{fid}")
    await drain()
    assert await mongo.db.entities.count_documents({}) == 0
    assert await mongo.db.relationships.count_documents({}) == 0
    assert await mongo.db.entity_mentions.count_documents({}) == 0


# --- Research --------------------------------------------------------------------------------


async def _project(user, question="the river and the bees") -> str:
    r = await user.post("/api/v1/research/projects", json={"title": "Georgia", "question": question})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_research_run_end_to_end(user):
    await _library(user)
    pid = await _project(user)
    r = await user.post(f"/api/v1/research/projects/{pid}/runs", json={"depth": "standard"})
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert r.json()["estimate"]["is_estimate"]
    u = (await user.get("/api/v1/me/usage")).json()["usage"]
    assert u["research_reports"]["reserved"] == 1 and u["concurrent_research"]["reserved"] == 1
    res = await drain()
    run = (await user.get(f"/api/v1/research/runs/{run_id}")).json()
    job = await mongo.db.jobs.find_one({"type": "research.run"})
    assert run["status"] == "completed", (res, job.get("status"), job.get("error"))
    assert run["plan"]["subquestions"] and run["sources_found"] > 0
    events = (await user.get(f"/api/v1/research/runs/{run_id}/events")).json()["events"]
    phases = [e["data"].get("phase") for e in events if e["type"] == "phase"]
    for p in ("planning", "retrieving", "critiquing", "follow_up", "synthesizing", "validating"):
        assert p in phases
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    rep = (await user.get(f"/api/v1/reports/{run['report_id']}")).json()
    assert rep["status"] == "complete"
    md = rep["markdown"]
    assert "[E999]" not in md and "E999" not in str(rep["sections"])  # forged citations removed
    kinds = {f["heading"]: f["kind"] for f in rep["sections"]["findings"]}
    assert kinds["Main theme"] == "supported" and kinds["Uncited claim"] == "hypothesis"
    cited = [e for e in rep["evidence"] if e["cited"]]
    assert cited
    for e in cited:
        assert await mongo.db.chunks.find_one({"_id": ObjectId(e["chunk_id"])})
    for title in (
        "Executive summary",
        "Research questions",
        "Main findings",
        "Supporting evidence",
        "Contradictory evidence",
        "Limitations",
        "Open questions",
        "References",
    ):
        assert f"## {title}" in md
    u = (await user.get("/api/v1/me/usage")).json()["usage"]
    assert u["research_reports"]["used"] == 1 and u["research_reports"]["reserved"] == 0
    assert u["concurrent_research"]["reserved"] == 0
    exp = await user.get(f"/api/v1/reports/{run['report_id']}/export.md")
    assert exp.status_code == 200 and exp.text == md
    assert (await user.get(f"/api/v1/reports/{run['report_id']}/export.pdf")).status_code == 403  # Free: no PDF export


async def test_research_report_limit_and_concurrency(user):
    await _library(user)
    pid = await _project(user)
    assert (await user.post(f"/api/v1/research/projects/{pid}/runs", json={})).status_code == 202
    second = await user.post(f"/api/v1/research/projects/{pid}/runs", json={})
    assert second.status_code == 402  # Free: 1 concurrent research job
    assert second.json()["error"]["details"]["metric"] == "concurrent_research"
    await drain()
    third = await user.post(f"/api/v1/research/projects/{pid}/runs", json={})
    assert third.status_code == 402 and third.json()["error"]["details"]["metric"] == "research_reports"
    # Project limit: Free allows 1 active project.
    r = await user.post("/api/v1/research/projects", json={"title": "Another", "question": "something else entirely"})
    assert r.status_code == 402


async def test_research_cancel_releases_allowance(user):
    await _library(user)
    pid = await _project(user)
    run_id = (await user.post(f"/api/v1/research/projects/{pid}/runs", json={})).json()["id"]
    assert (await user.post(f"/api/v1/research/runs/{run_id}/cancel")).json()["status"] == "cancelled"
    run = (await user.get(f"/api/v1/research/runs/{run_id}")).json()
    assert run["status"] == "cancelled"
    u = (await user.get("/api/v1/me/usage")).json()["usage"]
    assert u["research_reports"]["used"] == 0 and u["research_reports"]["reserved"] == 0 and u["concurrent_research"]["reserved"] == 0


async def test_research_survives_worker_restart(user):
    await _library(user)
    pid = await _project(user)
    run_id = (await user.post(f"/api/v1/research/projects/{pid}/runs", json={})).json()["id"]
    run = await mongo.db.research_jobs.find_one({"_id": ObjectId(run_id)})
    # Simulate a worker that completed planning, then crashed (lease expires).
    job = await queue.claim("crashing-worker", ["research.run"])
    await mongo.db.research_jobs.update_one(
        {"_id": run["_id"]},
        {
            "$set": {
                "state": {"plan": {"title": "T", "outline": [], "subquestions": [{"id": "Q1", "question": "What about the river?"}]}},
                "started_at": utcnow(),
                "status": "running",
            },
            "$inc": {"usage.calls": 1},
        },
    )
    await mongo.db.jobs.update_one({"_id": job["_id"]}, {"$set": {"lease_expires_at": utcnow() - timedelta(seconds=1)}})
    FAKE_LLM.calls.clear()
    await drain()
    assert "research_plan" not in FAKE_LLM.calls  # resumed after planning
    final = await mongo.db.research_jobs.find_one({"_id": run["_id"]})
    assert final["status"] == "completed" and final["report_id"]


async def test_research_budget_exhaustion_produces_incomplete_report(user):
    await _library(user)
    plan = await mongo.db.plans.find_one({"slug": "free"})
    await mongo.db.plan_versions.update_one(
        {"plan_id": plan["_id"], "version": 1}, {"$set": {"entitlements.max_ai_calls_per_operation": 1}}
    )
    pid = await _project(user)
    run_id = (await user.post(f"/api/v1/research/projects/{pid}/runs", json={})).json()["id"]
    await drain()
    run = (await user.get(f"/api/v1/research/runs/{run_id}")).json()
    assert run["status"] == "incomplete" and run["stop_reason"]
    rep = (await user.get(f"/api/v1/reports/{run['report_id']}")).json()
    assert rep["status"] == "incomplete" and "Incomplete report" in rep["markdown"]
    assert (
        FAKE_LLM.calls.count("research_plan") + FAKE_LLM.calls.count("research_synthesis") + FAKE_LLM.calls.count("research_critique") <= 1
    )


async def test_premium_pdf_export_and_cross_document(user):
    await _library(user)
    await _premium_sub(ObjectId(user.user["id"]))
    pid = await _project(user)
    srcs = [str(s["_id"]) async for s in mongo.db.books.find({})]
    r = await user.post(f"/api/v1/research/projects/{pid}/runs", json={"filters": {"collection_id": None, "source_ids": srcs}})
    assert r.status_code == 202
    await drain()
    run = (await user.get(f"/api/v1/research/runs/{r.json()['id']}")).json()
    pdf = await user.get(f"/api/v1/reports/{run['report_id']}/export.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


async def test_cross_document_research_gated_on_free(user):
    await _library(user)
    pid = await _project(user)
    r = await user.post(f"/api/v1/research/projects/{pid}/runs", json={"filters": {"source_ids": [str(ObjectId()), str(ObjectId())]}})
    assert r.status_code == 403 and r.json()["error"]["details"]["feature"] == "cross_document_research"


async def test_research_isolation(user, other_user):
    await _library(user)
    pid = await _project(user)
    run_id = (await user.post(f"/api/v1/research/projects/{pid}/runs", json={})).json()["id"]
    await drain()
    run = (await user.get(f"/api/v1/research/runs/{run_id}")).json()
    for path in (
        f"/api/v1/research/projects/{pid}",
        f"/api/v1/research/runs/{run_id}",
        f"/api/v1/research/runs/{run_id}/events",
        f"/api/v1/reports/{run['report_id']}",
    ):
        assert (await other_user.get(path)).status_code == 404, path
    assert (await other_user.post(f"/api/v1/research/runs/{run_id}/cancel")).status_code == 404


async def test_findings_require_own_passages(user, other_user):
    await _library(user)
    chunk = await mongo.db.chunks.find_one({})
    ok = await user.post("/api/v1/findings", json={"text": "Bees dance", "chunk_ids": [str(chunk["_id"])]})
    assert ok.status_code == 201
    bad = await other_user.post("/api/v1/findings", json={"text": "steal", "chunk_ids": [str(chunk["_id"])]})
    assert bad.status_code == 404
    items = (await user.get("/api/v1/findings")).json()["items"]
    assert items[0]["citations"][0]["chunk_id"] == str(chunk["_id"])
