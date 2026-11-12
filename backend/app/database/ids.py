"""Identifier helpers. ObjectIds are used for all primary keys and exposed as hex strings."""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.core.errors import NotFound


def oid(value: str | ObjectId, *, what: str = "resource") -> ObjectId:
    """Parse a client-supplied id. Malformed ids are reported as not-found to avoid enumeration hints."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise NotFound(f"{what.capitalize()} not found.") from exc


def maybe_oid(value: str | None) -> ObjectId | None:
    if not value:
        return None
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def sid(value: Any) -> str | None:
    return str(value) if value is not None else None
