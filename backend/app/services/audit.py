"""Append-only audit log.

The application exposes no update or delete operation for ``audit_events``. In production,
the API's database user should additionally be granted only ``insert``/``find`` on this
collection (see docs/security-privacy.md) so the log is immutable even if the app is compromised.
"""

from __future__ import annotations

import hashlib
from typing import Any

from bson import ObjectId

from app.core.timeutil import utcnow
from app.database.client import get_db
from app.observability.logging import redact, request_id_var


async def record(
    action: str,
    *,
    actor_id: ObjectId | None = None,
    actor_type: str = "user",
    target_type: str | None = None,
    target_id: Any = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> ObjectId:
    doc = {
        "action": action,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "target_type": target_type,
        "target_id": str(target_id) if target_id is not None else None,
        "reason": reason,
        "details": redact(details or {}),
        "ip_hash": hashlib.sha256(ip.encode()).hexdigest()[:24] if ip else None,
        "request_id": request_id_var.get(),
        "created_at": utcnow(),
    }
    res = await get_db().audit_events.insert_one(doc)
    return res.inserted_id
