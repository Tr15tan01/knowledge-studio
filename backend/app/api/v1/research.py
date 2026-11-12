"""Deep research: projects, runs (with persisted progress + SSE), reports, exports, findings."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import Field

from app.api.v1.ask import FiltersIn
from app.auth.deps import Workspace
from app.billing.entitlements import require_feature, resolve
from app.core.errors import NotFound, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.database.ids import oid
from app.jobs import queue
from app.research import service
from app.research.export import ExportTooLong, render_pdf
from app.retrieval.budget import estimate_research
from app.schemas.common import APIModel, iso, paging

router = APIRouter(tags=["research"])


class ProjectIn(APIModel):
    title: str = Field(min_length=2, max_length=160)
    question: str = Field(min_length=5, max_length=2000)


class RunIn(APIModel):
    question: str | None = Field(default=None, min_length=5, max_length=2000)
    filters: FiltersIn = Field(default_factory=FiltersIn)
    depth: Literal["quick", "standard", "thorough"] = "standard"


def project_out(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(p["_id"]),
        "title": p["title"],
        "question": p["question"],
        "status": p["status"],
        "run_count": p.get("run_count", 0),
        "last_run_id": str(p["last_run_id"]) if p.get("last_run_id") else None,
        "created_at": iso(p["created_at"]),
        "updated_at": iso(p.get("updated_at")),
    }


def run_out(r: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    st = r.get("state") or {}
    return {
        "id": str(r["_id"]),
        "project_id": str(r["project_id"]),
        "question": r["question"],
        "status": r["status"],
        "phase": r.get("phase"),
        "depth": r.get("depth"),
        "budget": r.get("budget"),
        "estimate": r.get("estimate"),
        "plan": st.get("plan"),
        "sources_found": len(st.get("evidence_index", {})),
        "gaps": [g.get("missing") for g in ((st.get("critique") or {}).get("gaps") or [])][:8],
        "conflicts": len((st.get("critique") or {}).get("conflicts") or []),
        "usage": r.get("usage"),
        "report_id": str(r["report_id"]) if r.get("report_id") else None,
        "stop_reason": r.get("stop_reason"),
        "error": r.get("error"),
        "cancel_requested": r.get("cancel_requested", False),
        "job_status": (job or {}).get("status"),
        "progress": (job or {}).get("progress"),
        "created_at": iso(r["created_at"]),
        "finished_at": iso(r.get("finished_at")),
    }


@router.get("/research/projects")
async def list_projects(ws: Workspace, status: Literal["active", "archived", "all"] = "active") -> dict[str, Any]:
    flt = ws.scope(user_id=ws.user_id)
    if status != "all":
        flt["status"] = status
    items = [project_out(p) async for p in get_db().research_projects.find(flt).sort("updated_at", -1).limit(200)]
    r = await resolve(ws.account_id)
    return {"items": items, "limit": r.entitlements.max_saved_research_projects}


@router.post("/research/projects", status_code=201)
async def create_project(body: ProjectIn, ws: Workspace) -> dict[str, Any]:
    return project_out(await service.create_project(ws, body.title, body.question))


@router.get("/research/projects/{project_id}")
async def get_project(project_id: str, ws: Workspace) -> dict[str, Any]:
    p = await service.get_project(ws, project_id)
    runs = [run_out(r) async for r in get_db().research_jobs.find({"project_id": p["_id"], **ws.scope()}).sort("created_at", -1).limit(50)]
    return {**project_out(p), "runs": runs}


class ProjectUpdate(APIModel):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    status: Literal["active", "archived"] | None = None


@router.patch("/research/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, ws: Workspace) -> dict[str, Any]:
    p = await service.get_project(ws, project_id)
    upd: dict[str, Any] = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if upd.get("status") == "active" and p["status"] != "active":
        r = await resolve(ws.account_id)
        active = await get_db().research_projects.count_documents(ws.scope(user_id=ws.user_id, status="active"))
        if active >= r.entitlements.max_saved_research_projects:
            raise ValidationFailed(
                "You've reached your plan's active research project limit. Archive another project first.", code="quota_exceeded"
            )
    upd["updated_at"] = utcnow()
    await get_db().research_projects.update_one({"_id": p["_id"]}, {"$set": upd})
    return project_out({**p, **upd})


@router.delete("/research/projects/{project_id}")
async def delete_project(project_id: str, ws: Workspace) -> dict[str, str]:
    p = await service.get_project(ws, project_id)
    if await get_db().research_jobs.count_documents({"project_id": p["_id"], "status": {"$in": ["queued", "running"]}}):
        raise ValidationFailed("Cancel running research before deleting this project.")
    await get_db().research_jobs.delete_many({"project_id": p["_id"]})
    await get_db().reports.delete_many({"project_id": p["_id"]})
    await get_db().research_projects.delete_one({"_id": p["_id"]})
    return {"status": "deleted"}


@router.post("/research/estimate")
async def estimate(ws: Workspace, depth: Literal["quick", "standard", "thorough"] = "standard") -> dict[str, Any]:
    r = await resolve(ws.account_id)
    steps = {
        "quick": max(2, r.entitlements.max_research_steps // 3),
        "standard": max(3, r.entitlements.max_research_steps // 2),
        "thorough": r.entitlements.max_research_steps,
    }[depth]
    return (await estimate_research(r, max_steps=steps)).public()


@router.post("/research/projects/{project_id}/runs", status_code=202)
async def start_run(
    project_id: str, body: RunIn, ws: Workspace, idempotency_key: str | None = Header(None, max_length=100)
) -> dict[str, Any]:
    p = await service.get_project(ws, project_id)
    run = await service.start_run(
        ws, p, question=body.question or p["question"], filters=body.filters.model_dump(), depth=body.depth, idempotency_key=idempotency_key
    )
    return run_out(run)


async def _run(ws: Any, run_id: str) -> dict[str, Any]:
    r = await get_db().research_jobs.find_one(ws.scope(_id=oid(run_id, what="research run"), user_id=ws.user_id))
    if not r:
        raise NotFound("Research run not found.")
    return r


@router.get("/research/runs/{run_id}")
async def get_run(run_id: str, ws: Workspace) -> dict[str, Any]:
    r = await _run(ws, run_id)
    job = await get_db().jobs.find_one({"_id": r.get("job_id")}, projection={"status": 1, "progress": 1}) if r.get("job_id") else None
    return run_out(r, job)


@router.post("/research/runs/{run_id}/cancel")
async def cancel_run(run_id: str, ws: Workspace) -> dict[str, str]:
    return {"status": await service.cancel_run(ws, run_id)}


def _event_out(e: dict[str, Any]) -> dict[str, Any]:
    return {"seq": e["seq"], "type": e["type"], "data": e.get("data", {}), "at": iso(e["created_at"])}


@router.get("/research/runs/{run_id}/events")
async def run_events(run_id: str, ws: Workspace, after: int = Query(0, ge=0)) -> dict[str, Any]:
    """Polling fallback: persisted progress events after a sequence number."""
    r = await _run(ws, run_id)
    events = await queue.events_since(r["job_id"], after) if r.get("job_id") else []
    return {"events": [_event_out(e) for e in events], "status": r["status"], "phase": r.get("phase")}


@router.get("/research/runs/{run_id}/stream")
async def run_stream(run_id: str, ws: Workspace, request: Request, last_event_id: str | None = Header(None)) -> StreamingResponse:
    """Server-Sent Events. Reconnects resume from Last-Event-ID; events are persisted."""
    r = await _run(ws, run_id)
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else int(request.query_params.get("after", "0") or 0)

    async def gen() -> AsyncIterator[str]:
        seq = start
        idle = 0
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                return
            events = await queue.events_since(r["job_id"], seq)
            for e in events:
                seq = e["seq"]
                yield f"id: {seq}\nevent: {e['type']}\ndata: {json.dumps(_event_out(e), default=str)}\n\n"
            run = await get_db().research_jobs.find_one({"_id": r["_id"]}, projection={"status": 1, "phase": 1, "report_id": 1})
            if run and run["status"] in ("completed", "incomplete", "failed", "cancelled") and not events:
                yield f"event: end\ndata: {json.dumps({'status': run['status'], 'report_id': str(run.get('report_id') or '')})}\n\n"
                return
            idle = 0 if events else idle + 1
            if idle and idle % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --- Reports ------------------------------------------------------------------------------


def report_out(rep: dict[str, Any], *, full: bool = True) -> dict[str, Any]:
    out = {
        "id": str(rep["_id"]),
        "title": rep["title"],
        "question": rep["question"],
        "status": rep["status"],
        "incomplete_reason": rep.get("incomplete_reason"),
        "project_id": str(rep["project_id"]),
        "research_job_id": str(rep["research_job_id"]),
        "created_at": iso(rep["created_at"]),
        "evidence_count": len(rep.get("evidence", [])),
    }
    if full:
        out.update(
            {
                "sections": rep.get("sections", {}),
                "research_questions": rep.get("research_questions", []),
                "conflicts": rep.get("conflicts", []),
                "evidence": rep.get("evidence", []),
                "markdown": rep.get("markdown", ""),
                "degraded": rep.get("degraded", []),
            }
        )
    return out


@router.get("/reports")
async def list_reports(ws: Workspace, page: int = 1, page_size: int = 20, project_id: str | None = None) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt = ws.scope(user_id=ws.user_id)
    if project_id:
        flt["project_id"] = oid(project_id, what="project")
    total = await get_db().reports.count_documents(flt)
    items = [
        report_out(x, full=False)
        async for x in get_db()
        .reports.find(flt, projection={"markdown": 0, "evidence.snippet": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _report(ws: Any, report_id: str) -> dict[str, Any]:
    rep = await get_db().reports.find_one(ws.scope(_id=oid(report_id, what="report"), user_id=ws.user_id))
    if not rep:
        raise NotFound("Report not found.")
    return rep


@router.get("/reports/{report_id}")
async def get_report(report_id: str, ws: Workspace) -> dict[str, Any]:
    return report_out(await _report(ws, report_id))


@router.delete("/reports/{report_id}")
async def delete_report(report_id: str, ws: Workspace) -> dict[str, str]:
    rep = await _report(ws, report_id)
    await get_db().reports.delete_one({"_id": rep["_id"]})
    return {"status": "deleted"}


def _fname(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]", "", title)[:60].strip() or "report"


@router.get("/reports/{report_id}/export.md")
async def export_md(report_id: str, ws: Workspace) -> Response:
    require_feature(await resolve(ws.account_id), "markdown_export", label="Markdown export")
    rep = await _report(ws, report_id)
    return Response(
        rep.get("markdown", ""),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_fname(rep["title"])}.md"'},
    )


@router.get("/reports/{report_id}/export.pdf")
async def export_pdf(report_id: str, ws: Workspace) -> Response:
    r = await resolve(ws.account_id)
    require_feature(r, "pdf_export", label="PDF export")
    rep = await _report(ws, report_id)
    try:
        pdf = await asyncio.to_thread(render_pdf, rep, max_pages=max(1, r.entitlements.max_pdf_export_pages))
    except ExportTooLong as exc:
        raise ValidationFailed(
            f"This report is {exc.pages} pages; your plan exports up to {r.entitlements.max_pdf_export_pages} pages. Use Markdown export instead.",
            code="export_too_long",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed("The PDF export failed. Markdown export is still available.", code="export_failed", status_code=500) from exc
    return Response(
        pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{_fname(rep["title"])}.pdf"'}
    )


# --- Saved findings -------------------------------------------------------------------------


class FindingIn(APIModel):
    text: str = Field(min_length=1, max_length=4000)
    note: str = Field(default="", max_length=2000)
    report_id: str | None = None
    message_id: str | None = None
    chunk_ids: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=10)


@router.get("/findings")
async def list_findings(ws: Workspace, page: int = 1, page_size: int = 30, q: str | None = None) -> dict[str, Any]:
    page, page_size, skip = paging(page, page_size)
    flt = ws.scope(user_id=ws.user_id)
    if q:
        flt["$or"] = [{"text": {"$regex": re.escape(q), "$options": "i"}}, {"note": {"$regex": re.escape(q), "$options": "i"}}]
    total = await get_db().research_findings.count_documents(flt)
    items = [
        {
            "id": str(f["_id"]),
            "text": f["text"],
            "note": f.get("note", ""),
            "tags": f.get("tags", []),
            "citations": f.get("citations", []),
            "report_id": str(f["report_id"]) if f.get("report_id") else None,
            "created_at": iso(f["created_at"]),
        }
        async for f in get_db().research_findings.find(flt).sort("created_at", -1).skip(skip).limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/findings", status_code=201)
async def create_finding(body: FindingIn, ws: Workspace) -> dict[str, Any]:
    db = get_db()
    citations = []
    ids = [oid(c, what="passage") for c in body.chunk_ids]
    async for c in db.chunks.find(ws.scope(_id={"$in": ids}), projection={"source_title": 1, "citation_label": 1, "source_id": 1}):
        citations.append(
            {
                "chunk_id": str(c["_id"]),
                "source_id": str(c["source_id"]),
                "source_title": c.get("source_title"),
                "citation_label": c.get("citation_label"),
            }
        )
    if len(citations) != len(ids):
        raise NotFound("One or more cited passages were not found.")
    doc = {
        **ws.scope(),
        "user_id": ws.user_id,
        "text": body.text,
        "note": body.note,
        "tags": body.tags,
        "citations": citations,
        "report_id": oid(body.report_id) if body.report_id else None,
        "message_id": oid(body.message_id) if body.message_id else None,
        "created_at": utcnow(),
    }
    res = await db.research_findings.insert_one(doc)
    return {"id": str(res.inserted_id)}


@router.delete("/findings/{finding_id}")
async def delete_finding(finding_id: str, ws: Workspace) -> dict[str, str]:
    res = await get_db().research_findings.delete_one(ws.scope(_id=oid(finding_id), user_id=ws.user_id))
    if not res.deleted_count:
        raise NotFound("Finding not found.")
    return {"status": "deleted"}
