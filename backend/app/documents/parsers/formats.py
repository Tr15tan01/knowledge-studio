"""Built-in parsers: PDF, DOCX, HTML, Markdown, TXT, CSV, JSON.

EPUB and others can be added by registering another function with ``@register``.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from app.documents.detect import DetectionError, decode_text, validate_docx_bytes
from app.documents.parsers.base import Block, ParsedDocument, ParseError, Section, register

MAX_PDF_PAGES = 5000
MAX_CSV_ROWS = 200_000
MAX_JSON_NODES = 500_000
CSV_ROWS_PER_BLOCK = 40

CHAPTER_RE = re.compile(
    r"^\s*(?:(chapter|part|book|section|prologue|epilogue|introduction|preface|appendix|თავი|ნაწილი)\b"
    r"(?:\s+[0-9ivxlcdm]+|\s+[a-z]+|\s*[0-9]+)?[\s.:—-]*.{0,80})$",
    re.IGNORECASE,
)
_WS = re.compile(r"[ \t ]+")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def normalize_text(t: str) -> str:
    """Normalise whitespace without destroying paragraph structure."""
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    t = _HYPHEN_BREAK.sub(r"\1\2", t)
    t = _WS.sub(" ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def split_paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) <= 1 and text.count("\n") > 8:
        # Text with hard-wrapped single newlines: rebuild paragraphs from sentence-ending lines.
        lines = [ln.strip() for ln in text.split("\n")]
        paras, cur = [], []
        for ln in lines:
            if not ln:
                continue
            cur.append(ln)
            if re.search(r'[.!?…:"”»)]$', ln) and len(" ".join(cur)) > 200:
                paras.append(" ".join(cur))
                cur = []
        if cur:
            paras.append(" ".join(cur))
    return [re.sub(r"\s*\n\s*", " ", p) for p in paras]


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    return 2 <= len(s) <= 90 and bool(CHAPTER_RE.match(s)) and not s.endswith((",", ";"))


def _sections_from_paragraphs(paras: list[tuple[str, int | None, dict[str, Any]]], default_title: str) -> list[Section]:
    sections: list[Section] = []
    current = Section(title=default_title, level=1, path=[default_title])
    for text, page, loc in paras:
        if _is_heading_line(text):
            if current.blocks:
                sections.append(current)
            current = Section(title=text.strip()[:200], level=1, path=[text.strip()[:200]])
            continue
        current.blocks.append(Block(text=text, page=page, location=loc))
    if current.blocks:
        sections.append(current)
    return [s for s in sections if s.blocks]


# --- PDF ---------------------------------------------------------------------------------


@register("pdf")
def parse_pdf(data: bytes, filename: str) -> ParsedDocument:
    from pypdf import PdfReader
    from pypdf.errors import FileNotDecryptedError, PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ParseError("encrypted_pdf", "This PDF is password-protected. Remove the password and upload it again.")
            except (FileNotDecryptedError, NotImplementedError) as exc:
                raise ParseError("encrypted_pdf", "This PDF is password-protected. Remove the password and upload it again.") from exc
        n_pages = len(reader.pages)
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise ParseError("corrupt_file", "The PDF could not be read; it may be damaged.") from exc
    if n_pages == 0:
        raise ParseError("empty_document", "The PDF has no pages.")
    if n_pages > MAX_PDF_PAGES:
        raise ParseError("too_many_pages", f"PDFs are limited to {MAX_PDF_PAGES} pages.")

    meta: dict[str, Any] = {}
    title = author = None
    try:
        info: dict[str, Any] = dict(reader.metadata or {})
        title = (info.get("/Title") or "").strip() or None
        author = (info.get("/Author") or "").strip() or None
        meta = {
            k.strip("/"): str(v)[:300]
            for k, v in info.items()
            if k in ("/Title", "/Author", "/Subject", "/Creator", "/Producer", "/CreationDate")
        }
    except Exception:  # noqa: BLE001 - metadata is optional
        pass

    page_texts: list[str] = []
    warnings: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            page_texts.append(normalize_text(page.extract_text() or ""))
        except Exception:  # noqa: BLE001
            page_texts.append("")
            warnings.append(f"Text could not be extracted from page {i + 1}.")

    total_chars = sum(len(t) for t in page_texts)
    if total_chars / max(1, n_pages) < 25:
        return ParsedDocument(
            file_type="pdf",
            title=title,
            author=author,
            page_count=n_pages,
            metadata=meta,
            needs_ocr=True,
            warnings=["This PDF appears to be scanned images without a text layer. OCR is required to index it."],
        )

    # Outline (bookmarks) gives authoritative chapter boundaries when present.
    starts: list[tuple[int, str, int]] = []

    def walk(items: Any, level: int) -> None:
        for it in items:
            if isinstance(it, list):
                walk(it, level + 1)
                continue
            try:
                pno = reader.get_destination_page_number(it)
                t = str(it.title).strip()
                if t and pno is not None and pno >= 0:
                    starts.append((pno, t[:200], level))
            except Exception:  # noqa: BLE001,S112
                continue

    try:
        walk(reader.outline, 1)
    except Exception:  # noqa: BLE001
        starts = []

    sections: list[Section] = []
    if starts:
        top = sorted({(p, t, lv) for p, t, lv in starts if lv <= 2}, key=lambda x: (x[0], x[2]))
        if top and top[0][0] > 0:
            top.insert(0, (0, title or "Front matter", 1))
        for idx, (pno, t, lv) in enumerate(top):
            end = top[idx + 1][0] if idx + 1 < len(top) else n_pages
            sec = Section(title=t, level=lv, path=[t])
            for p in range(pno, max(pno + 1, end)):
                if p >= n_pages:
                    break
                for k, para in enumerate(split_paragraphs(page_texts[p])):
                    sec.blocks.append(Block(text=para, page=p + 1, location={"page": p + 1, "paragraph": k}))
            if sec.blocks:
                sections.append(sec)
    if not sections:
        paras: list[tuple[str, int | None, dict[str, Any]]] = [
            (para, p + 1, {"page": p + 1, "paragraph": k})
            for p, text in enumerate(page_texts)
            for k, para in enumerate(split_paragraphs(text))
        ]
        sections = _sections_from_paragraphs(paras, title or _stem(filename))
    if not sections:
        raise ParseError("empty_document", "No readable text was found in this PDF.")
    return ParsedDocument(
        file_type="pdf", title=title, author=author, page_count=n_pages, sections=sections, metadata=meta, warnings=warnings
    )


def _stem(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[0][:200] or "Untitled"


# --- DOCX --------------------------------------------------------------------------------


@register("docx")
def parse_docx(data: bytes, filename: str) -> ParsedDocument:
    try:
        validate_docx_bytes(data)
    except DetectionError as exc:
        raise ParseError(exc.code, exc.message) from exc
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ParseError("corrupt_file", "The DOCX file could not be read.") from exc
    cp = d.core_properties
    title = (cp.title or "").strip() or None
    author = (cp.author or "").strip() or None
    sections: list[Section] = []
    stack: list[str] = []
    current = Section(title=title or _stem(filename), level=1, path=[title or _stem(filename)])
    para_idx = 0
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = normalize_text(p.text)
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"(?i)heading\s*(\d)", style)
            if m or style.lower() == "title":
                level = int(m.group(1)) if m else 1
                if current.blocks:
                    sections.append(current)
                stack = stack[: level - 1] + [text[:200]]
                current = Section(title=text[:200], level=level, path=list(stack))
                if style.lower() == "title" and not title:
                    title = text[:200]
                continue
            para_idx += 1
            current.blocks.append(Block(text=text, location={"paragraph": para_idx}))
        elif tag == "tbl":
            t = Table(child, d)
            rows = []
            for r in t.rows[:2000]:
                cells = [normalize_text(c.text) for c in r.cells]
                rows.append(" | ".join(cells))
            if rows:
                para_idx += 1
                current.blocks.append(Block(text="\n".join(rows), location={"paragraph": para_idx, "table": True}))
    if current.blocks:
        sections.append(current)
    if not sections:
        raise ParseError("empty_document", "No readable text was found in this document.")
    created = cp.created.isoformat() if cp.created else None
    return ParsedDocument(
        file_type="docx",
        title=title,
        author=author,
        publication_date=created,
        sections=sections,
        metadata={"docx_core": {"title": cp.title, "author": cp.author}},
    )


# --- HTML --------------------------------------------------------------------------------

_HTML_DROP = ("script", "style", "noscript", "iframe", "object", "embed", "svg", "template", "form", "button", "nav", "footer")
_HTML_BLOCKS = ("p", "li", "blockquote", "pre", "figcaption", "dd", "dt", "td", "th")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@register("html")
def parse_html(data: bytes, filename: str) -> ParsedDocument:
    from bs4 import BeautifulSoup

    text = decode_text(data)
    soup = BeautifulSoup(text, "lxml")
    for t in soup(list(_HTML_DROP)):
        t.decompose()
    title = soup.title.get_text(" ", strip=True)[:200] if soup.title else None
    author = None
    m = soup.find("meta", attrs={"name": "author"})
    if m and m.get("content"):
        author = str(m["content"])[:200]
    lang = (soup.html.get("lang") if soup.html else None) or None
    root = soup.body or soup
    sections: list[Section] = []
    stack: list[str] = []
    current = Section(title=title or _stem(filename), level=1, path=[title or _stem(filename)])
    idx = 0
    wanted = set(_HEADINGS) | set(_HTML_BLOCKS)
    for el in root.find_all(list(wanted)):
        if any(getattr(p, "name", None) in _HTML_BLOCKS for p in el.parents):
            continue
        txt = normalize_text(el.get_text(" ", strip=True))
        if not txt:
            continue
        if el.name in _HEADINGS:
            level = int(el.name[1])
            if current.blocks:
                sections.append(current)
            stack = stack[: level - 1] + [txt[:200]]
            current = Section(title=txt[:200], level=level, path=list(stack))
            continue
        idx += 1
        current.blocks.append(Block(text=txt, location={"paragraph": idx}))
    if current.blocks:
        sections.append(current)
    if not sections:
        body = normalize_text(root.get_text("\n", strip=True))
        if body:
            sections = [
                Section(
                    title=title or _stem(filename),
                    path=[title or _stem(filename)],
                    blocks=[Block(text=p, location={"paragraph": i}) for i, p in enumerate(split_paragraphs(body))],
                )
            ]
    if not sections:
        raise ParseError("empty_document", "No readable text was found in this HTML file.")
    return ParsedDocument(file_type="html", title=title, author=author, language=lang if isinstance(lang, str) else None, sections=sections)


# --- Markdown ----------------------------------------------------------------------------


@register("markdown")
def parse_markdown(data: bytes, filename: str) -> ParsedDocument:
    text = decode_text(data).replace("\r\n", "\n")
    title = None
    front: dict[str, str] = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            for ln in text[4:end].splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    front[k.strip().lower()] = v.strip().strip("\"'")
            text = text[end + 4 :]
    title = front.get("title")
    sections: list[Section] = []
    stack: list[str] = []
    current = Section(title=title or _stem(filename), level=1, path=[title or _stem(filename)])
    buf: list[str] = []
    in_code = False
    line_no = 0
    start_line = 1

    def flush() -> None:
        nonlocal buf
        para = "\n".join(buf).strip()
        if para:
            current.blocks.append(
                Block(
                    text=para if in_code else re.sub(r"!\[[^\]]*\]\([^)]*\)", "", para),
                    location={"line_start": start_line, "line_end": line_no},
                )
            )
        buf = []

    for line_no, line in enumerate(text.split("\n"), start=1):  # noqa: B007
        if line.strip().startswith("```"):
            buf.append(line)
            if in_code:
                flush()
                start_line = line_no + 1
            in_code = not in_code
            continue
        if in_code:
            buf.append(line)
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            heading = m.group(2)[:200]
            if level == 1 and not title:
                title = heading
            if current.blocks:
                sections.append(current)
            stack = stack[: level - 1] + [heading]
            current = Section(title=heading, level=level, path=list(stack))
            start_line = line_no + 1
            continue
        if not line.strip():
            flush()
            start_line = line_no + 1
            continue
        if not buf:
            start_line = line_no
        buf.append(line)
    flush()
    if current.blocks:
        sections.append(current)
    if not sections:
        raise ParseError("empty_document", "The Markdown file has no text.")
    return ParsedDocument(
        file_type="markdown", title=title, author=front.get("author"), publication_date=front.get("date"), sections=sections
    )


# --- Plain text --------------------------------------------------------------------------


@register("txt")
def parse_txt(data: bytes, filename: str) -> ParsedDocument:
    text = normalize_text(decode_text(data))
    if not text:
        raise ParseError("empty_document", "The text file is empty.")
    paras: list[tuple[str, int | None, dict[str, Any]]] = []
    offset = 0
    for i, p in enumerate(split_paragraphs(text)):
        paras.append((p, None, {"paragraph": i + 1, "char_offset": offset}))
        offset += len(p) + 2
    sections = _sections_from_paragraphs(paras, _stem(filename))
    return ParsedDocument(file_type="txt", title=None, sections=sections)


# --- CSV ---------------------------------------------------------------------------------


@register("csv")
def parse_csv(data: bytes, filename: str) -> ParsedDocument:
    text = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    csv.field_size_limit(1_000_000)
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ParseError("empty_document", "The CSV file is empty.") from exc
    except csv.Error as exc:
        raise ParseError("corrupt_file", "The CSV file could not be parsed.") from exc
    header = [h.strip() or f"column_{i + 1}" for i, h in enumerate(header)]
    section = Section(title=_stem(filename), path=[_stem(filename)])
    rows: list[str] = []
    row_start = 2
    n = 0
    try:
        for n, row in enumerate(reader, start=2):  # noqa: B007 - header is row 1
            if n - 1 > MAX_CSV_ROWS:
                raise ParseError("too_many_rows", f"CSV files are limited to {MAX_CSV_ROWS:,} rows.")
            if not any(c.strip() for c in row):
                continue
            rows.append(
                "; ".join(f"{header[i] if i < len(header) else f'column_{i + 1}'}: {v.strip()}" for i, v in enumerate(row) if v.strip())
            )
            if len(rows) >= CSV_ROWS_PER_BLOCK:
                section.blocks.append(Block(text="\n".join(rows), location={"row_start": row_start, "row_end": n}))
                rows, row_start = [], n + 1
    except csv.Error as exc:
        raise ParseError("corrupt_file", f"The CSV file could not be parsed near row {n}.") from exc
    if rows:
        section.blocks.append(Block(text="\n".join(rows), location={"row_start": row_start, "row_end": n}))
    if not section.blocks:
        raise ParseError("empty_document", "The CSV file has a header but no data rows.")
    return ParsedDocument(file_type="csv", sections=[section], metadata={"columns": header[:200], "rows": max(0, n - 1)})


# --- JSON --------------------------------------------------------------------------------


@register("json")
def parse_json(data: bytes, filename: str) -> ParsedDocument:
    text = decode_text(data)
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ParseError("corrupt_file", "The JSON file is not valid JSON.") from exc
    count = 0

    def flatten(node: Any, path: str, out: list[str], depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > MAX_JSON_NODES or depth > 64:
            raise ParseError("too_complex", "The JSON file is too large or deeply nested to index.")
        if isinstance(node, dict):
            for k, v in node.items():
                flatten(v, f"{path}.{k}" if path else str(k), out, depth + 1)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                flatten(v, f"{path}[{i}]", out, depth + 1)
        elif node is not None and str(node).strip():
            out.append(f"{path}: {node}")

    section = Section(title=_stem(filename), path=[_stem(filename)])
    groups: list[tuple[str, Any]] = (
        list(obj.items())
        if isinstance(obj, dict)
        else [(f"[{i}]", v) for i, v in enumerate(obj)]
        if isinstance(obj, list)
        else [("$", obj)]
    )
    for key, value in groups:
        lines: list[str] = []
        flatten(value, key if not key.startswith("[") else key, lines)
        for i in range(0, len(lines), 60):
            section.blocks.append(Block(text="\n".join(lines[i : i + 60]), location={"json_path": key}))
    if not section.blocks:
        raise ParseError("empty_document", "The JSON file has no text values.")
    return ParsedDocument(file_type="json", sections=[section])
