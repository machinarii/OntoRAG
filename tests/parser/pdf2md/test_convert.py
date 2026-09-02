from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from ontorag.parser.pdf2md.convert import (  # noqa: E402
    ConversionResult,
    Pdf2MdConversionError,
    convert_source,
)

pytestmark = pytest.mark.offline


def test_convert_text_pdf_yields_front_matter_and_body(text_pdf: Path, tmp_path: Path):
    result = convert_source(text_pdf, tmp_path / "work")
    assert isinstance(result, ConversionResult)
    assert result.markdown.startswith("---\n")
    assert "generator: pdf2md" in result.markdown
    assert "Body text of the page." in result.markdown
    assert result.stats["doc_type"] in {"book", "paper", "deck", "document"}


def test_convert_image_only_pdf_raises_conversion_error(
    image_pdf: Path, tmp_path: Path
):
    with pytest.raises(Pdf2MdConversionError, match="image-only"):
        convert_source(image_pdf, tmp_path / "work")


def test_convert_never_passes_figure_vlm(text_pdf: Path, tmp_path: Path, monkeypatch):
    import ontorag.parser.pdf2md._pdf2md as vendored

    seen = {}
    real_run = vendored.run

    def spy(args):
        seen["figure_vlm"] = args.figure_vlm
        return real_run(args)

    monkeypatch.setattr(vendored, "run", spy)
    convert_source(text_pdf, tmp_path / "work")
    assert seen["figure_vlm"] is None


def test_convert_missing_source_raises(tmp_path: Path):
    with pytest.raises(Pdf2MdConversionError, match="not found"):
        convert_source(tmp_path / "nope.pdf", tmp_path / "work")
