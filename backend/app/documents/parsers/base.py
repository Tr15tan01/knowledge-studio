"""Parser data model and registry.

Parsers turn bytes into a structure-preserving ``ParsedDocument``: sections (chapters,
headings) containing blocks (paragraphs, table rows) with source positions (pages, CSV
rows, character offsets) so citations can be reconstructed exactly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Block:
    text: str
    page: int | None = None
    location: dict[str, Any] = field(default_factory=dict)  # e.g. {"row_start": 2, "row_end": 40} or {"paragraph": 12}


@dataclass
class Section:
    title: str
    level: int = 1
    path: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)

    @property
    def page_start(self) -> int | None:
        pages = [b.page for b in self.blocks if b.page is not None]
        return min(pages) if pages else None

    @property
    def page_end(self) -> int | None:
        pages = [b.page for b in self.blocks if b.page is not None]
        return max(pages) if pages else None


@dataclass
class ParsedDocument:
    file_type: str
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    publication_date: str | None = None
    language: str | None = None
    page_count: int | None = None
    sections: list[Section] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    needs_ocr: bool = False

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for s in self.sections for b in s.blocks)

    def outline(self) -> list[dict[str, Any]]:
        return [
            {"title": s.title, "level": s.level, "page_start": s.page_start, "page_end": s.page_end, "blocks": len(s.blocks)}
            for s in self.sections
        ][:2000]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ParsedDocument:
        sections = [
            Section(title=s["title"], level=s["level"], path=s.get("path", []), blocks=[Block(**b) for b in s["blocks"]])
            for s in d.get("sections", [])
        ]
        return cls(**{**{k: v for k, v in d.items() if k != "sections"}, "sections": sections})


class ParseError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


ParserFn = Callable[[bytes, str], ParsedDocument]
_REGISTRY: dict[str, ParserFn] = {}


def register(file_type: str) -> Callable[[ParserFn], ParserFn]:
    def deco(fn: ParserFn) -> ParserFn:
        _REGISTRY[file_type] = fn
        return fn

    return deco


def get_parser(file_type: str) -> ParserFn:
    from app.documents.parsers import formats  # noqa: F401  (registers built-in parsers)

    try:
        return _REGISTRY[file_type]
    except KeyError as exc:
        raise ParseError("unsupported_format", f"No parser for '{file_type}'.") from exc


def supported_types() -> list[str]:
    from app.documents.parsers import formats  # noqa: F401

    return sorted(_REGISTRY)
