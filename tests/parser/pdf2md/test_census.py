from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from ontorag.parser.pdf2md.census import Census, pdf_text_layer_census  # noqa: E402

pytestmark = pytest.mark.offline


def test_text_pdf_is_not_image_only(text_pdf: Path):
    census = pdf_text_layer_census(text_pdf)
    assert census == Census(pages=2, text_pages=2)
    assert census.image_only is False


def test_image_only_pdf_is_detected(image_pdf: Path):
    census = pdf_text_layer_census(image_pdf)
    assert census.pages == 2 and census.text_pages == 0
    assert census.image_only is True


def test_blank_page_pdf_counts_as_image_only(tmp_path: Path):
    """A page with neither text nor image is still glyphless: OCR is the only
    way to find out whether it carries anything (PyMuPDF cannot even save a
    zero-page document, so 'no pages' is not a reachable input)."""
    import pymupdf

    path = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    census = pdf_text_layer_census(path)
    assert census == Census(pages=1, text_pages=0)
    assert census.image_only is True
