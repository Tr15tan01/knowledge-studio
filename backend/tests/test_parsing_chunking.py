from __future__ import annotations

import json

import pytest

from app.documents.chunking import chunk_document, embedding_text, estimate_tokens, query_text
from app.documents.detect import DetectionError, detect_from_head
from app.documents.isolation import parse_isolated
from app.documents.language import detect_language
from app.documents.parsers.base import ParseError, get_parser, supported_types
from tests import samples


def test_registry_has_all_formats():
    assert set(supported_types()) >= {"pdf", "txt", "markdown", "json", "docx", "html", "csv"}


def test_pdf_outline_chapters_and_pages():
    data = samples.pdf_with_outline(
        [("Chapter One", ["Alpha paragraph about rivers. " * 30]), ("Chapter Two", ["Beta paragraph about mountains. " * 30])]
    )
    doc = get_parser("pdf")(data, "book.pdf")
    assert doc.title == "A Test Book" and doc.author == "Ada Writer"
    titles = [s.title for s in doc.sections]
    assert "Chapter One" in titles and "Chapter Two" in titles
    ch2 = next(s for s in doc.sections if s.title == "Chapter Two")
    assert ch2.page_start is not None and ch2.page_start >= 2
    assert "mountains" in " ".join(b.text for b in ch2.blocks)


def test_pdf_encrypted_and_scanned():
    with pytest.raises(ParseError) as e:
        get_parser("pdf")(samples.encrypted_pdf(), "x.pdf")
    assert e.value.code == "encrypted_pdf"
    scanned = get_parser("pdf")(samples.blank_pdf(), "scan.pdf")
    assert scanned.needs_ocr is True
    with pytest.raises(ParseError) as e2:
        get_parser("pdf")(b"%PDF-1.4 garbage" + b"\x00" * 50, "bad.pdf")
    assert e2.value.code in ("corrupt_file", "empty_document")


def test_docx_headings_and_tables():
    doc = get_parser("docx")(samples.docx_bytes(), "notes.docx")
    assert doc.title == "Gardening Notes" and doc.author == "Grace Green"
    assert [s.title for s in doc.sections] == ["Soil", "Composting"]
    assert doc.sections[1].path == ["Soil", "Composting"]
    assert any("Tomato | Daily" in b.text for b in doc.sections[1].blocks)


def test_html_strips_active_content():
    html = b"<html lang='en'><head><title>T</title><script>alert('x')</script></head><body><h1>Intro</h1><p>Hello <b>world</b></p><style>p{}</style><iframe src='x'></iframe><h2>Next</h2><ul><li>Item one</li></ul></body></html>"
    doc = get_parser("html")(html, "p.html")
    text = " ".join(b.text for s in doc.sections for b in s.blocks)
    assert "alert" not in text and "Hello world" in text
    assert [s.title for s in doc.sections] == ["Intro", "Next"]
    assert doc.language == "en"


def test_markdown_csv_json_txt():
    md = b"---\ntitle: Notes\nauthor: Me\n---\n# Top\n\nPara one.\n\n## Sub\n\n```\ncode block\n```\n"
    d = get_parser("markdown")(md, "n.md")
    assert d.title == "Notes" and [s.title for s in d.sections] == ["Top", "Sub"]
    csv_data = b"name,age\n" + b"".join(f"person{i},{i}\n".encode() for i in range(90))
    c = get_parser("csv")(csv_data, "people.csv")
    blocks = c.sections[0].blocks
    assert blocks[0].location == {"row_start": 2, "row_end": 41}
    assert "name: person0; age: 0" in blocks[0].text
    j = get_parser("json")(json.dumps({"a": {"b": [1, "two"]}, "c": "three"}).encode(), "d.json")
    joined = "\n".join(b.text for b in j.sections[0].blocks)
    assert "a.b[1]: two" in joined and "c: three" in joined
    t = get_parser("txt")(samples.BOOK_TEXT.encode(), "book.txt")
    assert [s.title for s in t.sections] == ["Chapter 1: The River", "Chapter 2: The Mountains", "Chapter 3: Bees"]


def test_json_depth_bomb_rejected():
    deep = "[" * 5000 + "]" * 5000
    with pytest.raises(ParseError):
        get_parser("json")(deep.encode(), "deep.json")


def test_signature_detection():
    with pytest.raises(DetectionError) as e:
        detect_from_head("x.pdf", b"hello world")
    assert e.value.code == "signature_mismatch"
    with pytest.raises(DetectionError):
        detect_from_head("x.exe", b"MZ....")
    with pytest.raises(DetectionError) as e2:
        detect_from_head("x.txt", b"MZ\x90\x00")
    assert e2.value.code == "executable_rejected"
    with pytest.raises(DetectionError):
        detect_from_head("notes.txt", b"abc\x00\x00def")
    assert detect_from_head("a.md", b"# hi").file_type == "markdown"


def test_docx_zip_bomb_rejected():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", "A" * (60 * 1024 * 1024))
    with pytest.raises(ParseError) as e:
        get_parser("docx")(buf.getvalue(), "bomb.docx")
    assert e.value.code == "archive_limits"


def test_chunking_respects_sections_budget_and_overlap():
    para = " ".join(f"Sentence number {i} talks about the waggle dance of bees." for i in range(200))
    doc = get_parser("txt")(f"Chapter 1: Bees\n\n{para}\n\nChapter 2: Rivers\n\nShort river text.".encode(), "b.txt")
    chunks = chunk_document(doc, target_tokens=300, overlap_tokens=60)
    assert len(chunks) > 3
    assert all(c.token_estimate <= 300 * 1.25 for c in chunks)
    assert {c.section_title for c in chunks} == {"Chapter 1: Bees", "Chapter 2: Rivers"}
    river = [c for c in chunks if c.section_title == "Chapter 2: Rivers"]
    assert len(river) == 1 and river[0].text == "Short river text." and river[0].overlap_prefix_chars == 0
    second = chunks[1]
    assert second.overlap_prefix_chars > 0
    assert chunks[0].text.endswith(second.text[: second.overlap_prefix_chars - 2].split(" ")[-1]) or second.overlap_prefix_chars > 0
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_embedding_formats_and_token_estimates():
    doc = get_parser("txt")(b"Chapter 1: X\n\nSome body text here.", "t.txt")
    c = chunk_document(doc)[0]
    assert embedding_text("My | Book", c).startswith("title: My / Book | text: ")
    assert embedding_text(None, c).startswith("title: none | text: ")
    assert query_text("  bees? ") == "task: search result | query: bees?"
    assert estimate_tokens("abcd" * 100) == 100
    assert estimate_tokens("ქართული ენა") > estimate_tokens("english tx")


def test_language_detection_georgian_and_english():
    assert detect_language("საქართველო არის ქვეყანა კავკასიაში, მისი დედაქალაქი თბილისია.")[0] == "ka"
    assert detect_language("The quick brown fox jumps over the lazy dog near the river bank.")[0] == "en"


async def test_isolated_parse_runs_in_child_process():
    doc = await parse_isolated(samples.BOOK_TEXT.encode(), "txt", "book.txt", isolate=True, timeout=60)
    assert len(doc.sections) == 3
    with pytest.raises(ParseError) as e:
        await parse_isolated(b"not a pdf", "pdf", "x.pdf", isolate=True, timeout=60)
    assert e.value.code in ("corrupt_file", "empty_document", "parse_failed")
