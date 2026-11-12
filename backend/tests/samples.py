"""Generate small sample documents for tests."""

from __future__ import annotations

import io


def pdf_with_outline(chapters: list[tuple[str, list[str]]], *, title: str = "A Test Book", author: str = "Ada Writer") -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(title)
    c.setAuthor(author)
    for ci, (heading, paragraphs) in enumerate(chapters):
        key = f"ch{ci}"
        c.bookmarkPage(key)
        c.addOutlineEntry(heading, key, level=0)
        y = 740
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, y, heading)
        y -= 30
        c.setFont("Helvetica", 11)
        for p in paragraphs:
            words = p.split()
            line = ""
            for w in words:
                if len(line) + len(w) > 85:
                    c.drawString(72, y, line)
                    y -= 14
                    line = ""
                    if y < 72:
                        c.showPage()
                        c.setFont("Helvetica", 11)
                        y = 740
                line += w + " "
            c.drawString(72, y, line)
            y -= 28
            if y < 100:
                c.showPage()
                c.setFont("Helvetica", 11)
                y = 740
        c.showPage()
    c.save()
    return buf.getvalue()


def blank_pdf(pages: int = 2) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for _ in range(pages):
        c.rect(50, 50, 100, 100)
        c.showPage()
    c.save()
    return buf.getvalue()


def encrypted_pdf() -> bytes:
    from pypdf import PdfReader, PdfWriter

    src = PdfReader(io.BytesIO(pdf_with_outline([("Secret", ["Hidden text " * 20])])))
    w = PdfWriter()
    for p in src.pages:
        w.add_page(p)
    w.encrypt(user_password="s3cret", owner_password="owner")
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def docx_bytes() -> bytes:
    import docx

    d = docx.Document()
    d.core_properties.title = "Gardening Notes"
    d.core_properties.author = "Grace Green"
    d.add_heading("Soil", level=1)
    d.add_paragraph("Healthy soil contains organic matter and earthworms that aerate it.")
    d.add_heading("Composting", level=2)
    d.add_paragraph("Compost turns kitchen scraps into nutrient-rich humus over several months.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Plant"
    t.cell(0, 1).text = "Water"
    t.cell(1, 0).text = "Tomato"
    t.cell(1, 1).text = "Daily"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


BOOK_TEXT = """Chapter 1: The River

The river Mtkvari flows through Tbilisi and has shaped the city's history for centuries. Merchants traded silk along its banks.

The old bridges connected the districts of the city, and travellers wrote about the sulfur baths near the river.

Chapter 2: The Mountains

The Caucasus mountains rise to the north. Shepherds move flocks to high pastures every summer in a practice called transhumance.

Kazbek is one of the highest peaks, and monasteries were built on its slopes. Climbers attempt the summit each July.

Chapter 3: Bees

Honeybees communicate the location of flowers through the waggle dance. The angle of the dance encodes direction relative to the sun.

Beekeepers in the mountains produce chestnut honey, which is dark and slightly bitter.
"""
