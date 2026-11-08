"""Minimal synthetic PDF builder for OFFLINE test fixtures only.

Used by tests to craft snapshot-version fixtures (e.g. three FelagPdf variants
of one logical document with different Dagf. dates). ASCII-only text (standard
Helvetica has no Faroese glyph coverage); real Faroese fixtures come from the
genuine PDFs in $HEIMDAL_FIXTURES_DIR (outside git, audit I-5).
"""
from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal single-page PDF that renders each list item as a line.

    pypdf round-trips this to '\\n'.join(lines) text, verified in tests.
    """
    parts = []
    for line in lines:
        esc = (line.replace("\\", r"\\")
                   .replace("(", r"\(")
                   .replace(")", r"\)"))
        parts.append(f"({esc}) Tj T*")
    stream = "BT /F1 11 Tf 14 TL 40 800 Td\n" + "\n".join(parts) + "\nET"
    data = stream.encode("latin-1", "replace")

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    content = DecodedStreamObject()
    content.set_data(data)
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font)}),
    })
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = resources
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_hostile_pdf(content: bytes, pages: int = 1) -> bytes:
    """`pages` pages that all draw ONE Flate-compressed content stream.

    Tiny on disk, unbounded pypdf work (audit D-04): pypdf caps one stream's
    decompressed size, not how often pages reuse it.
    """
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    stream = DecodedStreamObject()
    stream.set_data(content)
    contents = writer._add_object(stream.flate_encode(level=9))
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Contents")] = contents
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_pdf_text_roundtrip(lines: list[str]) -> str:
    """make_pdf() followed by pypdf extraction (used by tests to stay honest)."""
    from .extract import extract_text
    return extract_text(make_pdf(lines))