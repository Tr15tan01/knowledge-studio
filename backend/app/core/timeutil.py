"""Time helpers. All persisted timestamps are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def require_utc(dt: datetime | None) -> datetime:
    """Like ensure_utc for fields that are always set; raises if the value is missing."""
    out = ensure_utc(dt)
    if out is None:
        raise ValueError("expected a datetime")
    return out


def ensure_utc(dt: datetime | None) -> datetime | None:
    """MongoDB returns naive datetimes unless tz_aware=True; normalise defensively."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    return ensure_utc(datetime.fromisoformat(v))
