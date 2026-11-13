from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import Field

from app.api.v1.auth import user_out
from app.auth.cookies import clear_session_cookies
from app.auth.deps import Auth, require_recent_auth
from app.auth.service import normalize_email, revoke_all_sessions
from app.billing.entitlements import resolve, usage_summary
from app.billing.schema import FEATURE_LABELS
from app.billing.storage_accounting import storage_summary
from app.core.errors import ValidationFailed
from app.core.timeutil import utcnow
from app.database.client import get_db
from app.jobs import queue
from app.schemas.common import APIModel
from app.services import audit

router = APIRouter(prefix="/me", tags=["account"])


class Preferences(APIModel):
    theme: Literal["system", "light", "dark", "blue", "white", "green", "sepia", "violet", "midnight", "black", "forest"] | None = None
    reduced_motion: bool | None = None
    answer_language: Literal["auto", "en", "ka"] | None = None


class ProfileUpdate(APIModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    preferences: Preferences | None = None


class DeleteAccountIn(APIModel):
    confirm_email: str = Field(max_length=320)


@router.get("")
async def me(auth: Auth) -> dict[str, Any]:
    linked = [a["provider"] async for a in get_db().oauth_accounts.find({"user_id": auth.user_id}, projection={"provider": 1})]
    return {**user_out(auth.user), "linked_providers": linked}


@router.patch("")
async def update_me(body: ProfileUpdate, auth: Auth) -> dict[str, Any]:
    upd: dict[str, Any] = {"updated_at": utcnow()}
    if body.display_name is not None:
        upd["display_name"] = body.display_name
    if body.preferences is not None:
        for k, v in body.preferences.model_dump(exclude_none=True).items():
            upd[f"preferences.{k}"] = v
    await get_db().users.update_one({"_id": auth.user_id}, {"$set": upd})
    user = await get_db().users.find_one({"_id": auth.user_id})
    return user_out(user or auth.user)


def entitlements_out(r: Any) -> dict[str, Any]:
    e = r.entitlements
    return {
        "plan": {"id": str(r.plan_id), "slug": r.plan_slug, "name": r.plan_name, "version": r.plan_version},
        "source": r.source,
        "subscription_status": r.subscription_status,
        "notices": r.notices,
        "period": {"start": r.period.start.isoformat(), "end": r.period.end.isoformat(), "kind": r.period.kind, "timezone": "UTC"},
        "limits": {k: v for k, v in e.model_dump().items() if k != "features"},
        "features": [{"key": k, "label": FEATURE_LABELS.get(k, k), "enabled": v} for k, v in e.features.items()],
        "active_grants": [{"kind": g["kind"], "expires_at": g["expires_at"].isoformat(), "reason": g.get("public_note")} for g in r.grants],
    }


@router.get("/usage")
async def my_usage(auth: Auth) -> dict[str, Any]:
    r = await resolve(auth.account_id)
    return {
        "entitlements": entitlements_out(r),
        "usage": await usage_summary(r),
        "storage": await storage_summary(r),
    }


@router.post("/export")
async def request_export(auth: Auth) -> dict[str, Any]:
    job_id = await queue.enqueue(
        "account.export",
        {"user_id": str(auth.user_id)},
        account_id=auth.account_id,
        dedupe_key=f"export:{auth.user_id}:{utcnow():%Y%m%d%H}",
    )
    await audit.record("account.export_requested", actor_id=auth.user_id)
    return {"job_id": str(job_id), "status": "queued"}


@router.delete("")
async def delete_account(body: DeleteAccountIn, auth: Auth, response: Response) -> dict[str, Any]:
    require_recent_auth(auth) if auth.user.get("password_hash") else None
    if normalize_email(body.confirm_email) != auth.user["email_normalized"]:
        raise ValidationFailed("Type your email address exactly to confirm deletion.")
    paid = await get_db().subscriptions.find_one(
        {"account_id": auth.account_id, "source": "paddle", "status": {"$in": ["active", "trialing", "past_due", "paused"]}}
    )
    if paid and (paid.get("scheduled_change") or {}).get("action") != "cancel":
        raise ValidationFailed(
            "Cancel your paid subscription before deleting your account so you aren't billed again.", code="active_subscription"
        )
    if auth.user.get("role") in ("ADMIN", "SUPER_ADMIN"):
        n = await get_db().users.count_documents({"role": "SUPER_ADMIN", "status": "active", "_id": {"$ne": auth.user_id}})
        if auth.user["role"] == "SUPER_ADMIN" and n == 0:
            raise ValidationFailed("You are the last super administrator. Promote another account before deleting this one.")
    await get_db().users.update_one({"_id": auth.user_id}, {"$set": {"status": "deleting", "deletion_requested_at": utcnow()}})
    await revoke_all_sessions(auth.user_id, reason="account_deleted")
    job_id = await queue.enqueue(
        "account.delete", {"user_id": str(auth.user_id)}, account_id=auth.account_id, dedupe_key=f"account_delete:{auth.user_id}"
    )
    await audit.record("account.deletion_requested", actor_id=auth.user_id, target_type="user", target_id=auth.user_id)
    clear_session_cookies(response)
    return {"status": "deletion_scheduled", "job_id": str(job_id)}


@router.get("/exports")
async def list_exports(auth: Auth) -> dict[str, Any]:
    items = [
        {
            "id": str(x["_id"]),
            "size_bytes": x["size_bytes"],
            "created_at": x["created_at"].isoformat(),
            "expires_at": x["expires_at"].isoformat(),
        }
        async for x in get_db()
        .data_exports.find({"user_id": auth.user_id, "expires_at": {"$gt": utcnow()}})
        .sort("created_at", -1)
        .limit(10)
    ]
    return {"items": items}


@router.get("/exports/{export_id}/download")
async def download_export(export_id: str, auth: Auth) -> Any:
    from fastapi.responses import RedirectResponse, StreamingResponse

    from app.core.config import get_settings
    from app.core.errors import NotFound
    from app.database.ids import oid
    from app.storage import storage_for

    x = await get_db().data_exports.find_one(
        {"_id": oid(export_id, what="export"), "user_id": auth.user_id, "expires_at": {"$gt": utcnow()}}
    )
    if not x:
        raise NotFound("Export not found or expired.")
    storage = storage_for(x)
    url = await storage.signed_download_url(
        x["storage_key"], filename="knowledge-studio-export.zip", ttl_seconds=get_settings().signed_url_ttl_seconds
    )
    if url:
        return RedirectResponse(url, status_code=302)
    return StreamingResponse(
        storage.open_read(x["storage_key"]),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="knowledge-studio-export.zip"', "Cache-Control": "private, no-store"},
    )
