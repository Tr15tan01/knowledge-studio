"""Structure-aware, token-aware chunking.

* Chunks never cross section (chapter/heading) boundaries.
* Paragraphs are packed up to a target token budget; oversized paragraphs are split on
  sentence boundaries, then on words as a last resort.
* Overlap re-uses whole trailing sentences from the previous chunk within the same section.
* Token counts use a deterministic offline estimator (no tokenizer download). Georgian and
  other non-Latin scripts are estimated conservatively. Budgets are configurable.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.documents.parsers.base import Block, ParsedDocument

_SENT_SPLIT = re.compile(r"(?<=[.!?…।。؟])\s+(?=[\"'“«(\[]?[A-ZА-ЯႠ-ჿ0-9])|(?<=[.!?…])\s+(?=[ა-ჰ])")
_WORD = re.compile(r"\S+")


def estimate_tokens(text: str) -> int:
    """Approximate LLM tokens: ~4 ASCII chars/token; non-ASCII scripts ~1.6 chars/token."""
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_chars
    return max(1, math.ceil(ascii_chars / 4 + other / 1.6))


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    return parts or [text]


@dataclass
class Chunk:
    ordinal: int
    text: str
    section_title: str
    section_path: list[str]
    page_start: int | None
    page_end: int | None
    locations: list[dict[str, Any]] = field(default_factory=list)
    token_estimate: int = 0
    content_hash: str = ""
    overlap_prefix_chars: int = 0

    def citation_label(self) -> str:
        if self.page_start:
            return (
                f"p. {self.page_start}"
                if self.page_start == self.page_end or not self.page_end
                else f"pp. {self.page_start}–{self.page_end}"
            )
        rows = [loc for loc in self.locations if "row_start" in loc]
        if rows:
            return f"rows {rows[0]['row_start']}–{rows[-1]['row_end']}"
        return self.section_title


@dataclass
class _Piece:
    text: str
    page: int | None
    location: dict[str, Any]
    tokens: int


def _pieces_for_block(b: Block, max_tokens: int) -> list[_Piece]:
    t = estimate_tokens(b.text)
    if t <= max_tokens:
        return [_Piece(b.text, b.page, b.location, t)]
    out: list[_Piece] = []
    buf: list[str] = []
    buf_t = 0
    for sent in split_sentences(b.text):
        st = estimate_tokens(sent)
        if st > max_tokens:  # pathological sentence: split by words
            words = _WORD.findall(sent)
            step = max(1, int(len(words) * max_tokens / st))
            for i in range(0, len(words), step):
                w = " ".join(words[i : i + step])
                out.append(_Piece(w, b.page, b.location, estimate_tokens(w)))
            continue
        if buf_t + st > max_tokens and buf:
            txt = " ".join(buf)
            out.append(_Piece(txt, b.page, b.location, estimate_tokens(txt)))
            buf, buf_t = [], 0
        buf.append(sent)
        buf_t += st
    if buf:
        txt = " ".join(buf)
        out.append(_Piece(txt, b.page, b.location, estimate_tokens(txt)))
    return out


def chunk_document(doc: ParsedDocument, *, target_tokens: int = 800, overlap_tokens: int = 120, max_chunks: int = 20000) -> list[Chunk]:
    if target_tokens < 50:
        raise ValueError("target_tokens too small")
    overlap_tokens = min(overlap_tokens, target_tokens // 3)
    chunks: list[Chunk] = []
    for section in doc.sections:
        pieces = [p for b in section.blocks for p in _pieces_for_block(b, target_tokens)]
        cur: list[_Piece] = []
        cur_t = 0
        overlap_text = ""

        def emit(section=section) -> None:  # bind the current section explicitly
            nonlocal cur, cur_t, overlap_text
            if not cur:
                return
            body = "\n\n".join(p.text for p in cur)
            text = (overlap_text + "\n\n" + body) if overlap_text else body
            pages = [p.page for p in cur if p.page is not None]
            locs: list[dict[str, Any]] = []
            for p in cur:
                if p.location and (not locs or locs[-1] != p.location):
                    locs.append(p.location)
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=text,
                    section_title=section.title,
                    section_path=section.path or [section.title],
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    locations=locs[:50],
                    token_estimate=estimate_tokens(text),
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    overlap_prefix_chars=len(overlap_text) + (2 if overlap_text else 0),
                )
            )
            if len(chunks) > max_chunks:
                raise ValueError(f"document produced more than {max_chunks} chunks")
            # Prepare overlap from trailing sentences of this chunk's own body.
            tail: list[str] = []
            tt = 0
            for s in reversed(split_sentences(body)):
                st = estimate_tokens(s)
                if tt + st > overlap_tokens:
                    break
                tail.insert(0, s)
                tt += st
            overlap_text = " ".join(tail)
            cur, cur_t = [], 0

        budget = target_tokens - overlap_tokens
        for p in pieces:
            if cur and cur_t + p.tokens > budget:
                emit()
            cur.append(p)
            cur_t += p.tokens
        emit()
    return chunks


def embedding_text(title: str | None, chunk: Chunk) -> str:
    """Gemini Embedding 2 retrieval-document format: ``title: {title} | text: {content}``."""
    t = (title or "none").replace("|", "/").strip()[:200] or "none"
    heading = " › ".join(chunk.section_path[-2:]) if chunk.section_path else ""
    content = f"{heading}\n{chunk.text}" if heading and heading.lower() != t.lower() else chunk.text
    return f"title: {t} | text: {content}"


def query_text(query: str) -> str:
    """Gemini Embedding 2 retrieval-query format."""
    return f"task: search result | query: {query.strip()}"
