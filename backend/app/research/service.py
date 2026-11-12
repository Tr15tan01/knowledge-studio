"""Research projects and runs (API side): authorization, quotas, reservations, cancellation."""

from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.auth.deps import WorkspaceContext
from app.billing import usage
from app.billing.entitlements import require_feature, reserve_metric, resolve
from app.core.errors import NotFound, QuotaExceeded, ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.database.ids import oid
from app.jobs import queue
from app.retrieval.budget import estimate_research, reserve_operation
from app.services import audit


async def create_project(ws: WorkspaceContext, title: str, question: str) -> dict[str, Any]:
    r = await resolve(ws.account_id)
    require_feature(r, "deep_research", label="Deep research")
    active = await get_db().research_projects.count_documents(ws.scope(user_id=ws.user_id, status="active"))
    limit = r.entitlements.max_saved_research_projects
    if active >= limit:
        raise QuotaExceeded(
            f"Your {r.plan_name} plan allows {limit} active research project{'s' if limit != 1 else ''}. Archive one or upgrade.",
            details={"metric": "research_projects", "limit": limit, "used": active, "upgrade_path": "/pricing"},
        )
    now = utcnow()
    doc = {
        **ws.scope(),
        "user_id": ws.user_id,
        "title": title,
        "question": question,
        "status": "active",
        "run_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    res = await get_db().research_projects.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_project(ws: WorkspaceContext, project_id: str) -> dict[str, Any]:
    p = await get_db().research_projects.find_one(ws.scope(_id=oid(project_id, what="project"), user_id=ws.user_id))
    if not p:
        raise NotFound("Research project not found.")
    return p


async def start_run(
    ws: WorkspaceContext, project: dict[str, Any], *, question: str, filters: dict[str, Any], depth: str, idempotency_key: str | None
) -> dict[str, Any]:
    r = await resolve(ws.account_id)
    require_feature(r, "deep_research", label="Deep research")
    if (len(filters.get("source_ids") or []) + len(filters.get("file_ids") or [])) >= 2 or filters.get("collection_id"):
        require_feature(r, "cross_document_research", label="Cross-document research on selected sources")
    if project.get("status") != "active":
        raise ValidationFailed("Reactivate this project before starting new research.")
    steps = {
        "quick": max(2, r.entitlements.max_research_steps // 3),
        "standard": max(3, r.entitlements.max_research_steps // 2),
        "thorough": r.entitlements.max_research_steps,
    }[depth]
    est = await estimate_research(r, max_steps=steps)
    run_id = ObjectId()
    key = idempotency_key or str(run_id)
    timeout = r.entitlements.max_operation_seconds
    concurrency = await reserve_metric(r, "concurrent_research", 1, idempotency_key=f"concurrent_research:{key}", ttl_seconds=timeout + 900)
    if concurrency.replayed:
        existing = await get_db().research_jobs.find_one({"idempotency_key": key, **ws.scope()})
        if existing:
            return existing
    try:
        hold = await reserve_operation(r, est, idempotency_key=key, ttl_seconds=timeout + 900)
    except Exception:
        await usage.release(concurrency.id, reason="denied")
        raise
    now = utcnow()
    budget = {
        "max_steps": steps,
        "max_retrieval_iterations": r.entitlements.max_retrieval_iterations,
        "max_ai_calls": r.entitlements.max_ai_calls_per_operation,
        "max_input_tokens_per_call": r.entitlements.max_input_tokens_per_operation,
        "max_output_tokens": r.entitlements.max_output_tokens_per_operation,
        "max_seconds": timeout,
        "compute_units": est.compute_units,
        "cost_usd_estimate": est.cost_usd,
        "long_report": r.has_feature("cross_document_research"),
    }
    doc = {
        "_id": run_id,
        **ws.scope(),
        "user_id": ws.user_id,
        "account_id": ws.account_id,
        "project_id": project["_id"],
        "question": question,
        "filters": filters,
        "depth": depth,
        "status": "queued",
        "phase": "queued",
        "budget": budget,
        "estimate": est.public(),
        "reservations": {m: res.id for m, res in hold.reservations.items()} | {"concurrent_research": concurrency.id},
        "idempotency_key": key,
        "state": {},
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "created_at": now,
        "updated_at": now,
    }
    await get_db().research_jobs.insert_one(doc)
    job_id = await queue.enqueue(
        "research.run",
        {"research_job_id": str(run_id)},
        account_id=ws.account_id,
        workspace_id=ws.workspace_id,
        priority=10 if r.has_feature("priority_processing") else 0,
        dedupe_key=f"research:{run_id}",
        timeout_seconds=timeout,
        max_attempts=3,
    )
    await get_db().research_jobs.update_one({"_id": run_id}, {"$set": {"job_id": job_id}})
    await get_db().research_projects.update_one(
        {"_id": project["_id"]}, {"$inc": {"run_count": 1}, "$set": {"updated_at": now, "last_run_id": run_id}}
    )
    await audit.record("research.started", actor_id=ws.user_id, target_type="research_job", target_id=run_id, details={"depth": depth})
    doc["job_id"] = job_id
    return doc


async def cancel_run(ws: WorkspaceContext, run_id: str) -> str:
    run = await get_db().research_jobs.find_one(ws.scope(_id=oid(run_id, what="research run"), user_id=ws.user_id))
    if not run:
        raise NotFound("Research run not found.")
    if run["status"] in ("completed", "failed", "cancelled", "incomplete"):
        return "already_finished"
    result = await queue.request_cancel(run["job_id"])
    if result == "cancelled":  # never started: settle immediately
        from app.research.agent import settle

        await settle(run, outcome="cancelled")
        await get_db().research_jobs.update_one(
            {"_id": run["_id"]}, {"$set": {"status": "cancelled", "phase": "cancelled", "updated_at": utcnow()}}
        )
    else:
        await get_db().research_jobs.update_one({"_id": run["_id"]}, {"$set": {"cancel_requested": True, "updated_at": utcnow()}})
    return result
