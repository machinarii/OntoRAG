"""Shared fixtures: tiny PDFs synthesised with PyMuPDF (text page / image-only page)."""

from __future__ import annotations

from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")

# 1x1 white PNG
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02"
    b"\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_text_pdf(path: Path, pages: int = 2, text: str = "Chapter 1: Arrival") -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} page {i + 1}", fontsize=18)
        page.insert_text((72, 120), "Body text of the page. " * 20, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def make_image_only_pdf(path: Path, pages: int = 2) -> Path:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(72, 72, 300, 300), stream=PNG_BYTES)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    return make_text_pdf(tmp_path / "book.pdf")


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    return make_image_only_pdf(tmp_path / "scan.pdf")
