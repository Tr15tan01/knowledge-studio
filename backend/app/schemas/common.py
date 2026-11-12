from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OutModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Page(OutModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Message(OutModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def paging(page: int, page_size: int, max_size: int = 100) -> tuple[int, int, int]:
    page = max(1, page)
    page_size = min(max(1, page_size), max_size)
    return page, page_size, (page - 1) * page_size
