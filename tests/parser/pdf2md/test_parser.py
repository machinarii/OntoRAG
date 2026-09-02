from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

import ontorag.pipeline as _pipeline  # noqa: E402
from ontorag.constants import FULL_DOCS_FORMAT_ONTORAG  # noqa: E402
from ontorag.parser.base import ParseContext  # noqa: E402
from ontorag.parser.pdf2md import parser as p2m  # noqa: E402
from ontorag.parser.pdf2md.convert import (  # noqa: E402
    ConversionResult,
    Pdf2MdConversionError,
)
from ontorag.parser.pdf2md.parser import Pdf2MdParser  # noqa: E402
from tests.parser.pdf2md.conftest import make_text_pdf  # noqa: E402

pytestmark = pytest.mark.offline

_MD = "---\ntitle: T\nauthors:\n  - A\n---\n\n# T\n\nBody text.\n"


class _FakeRag:
    def __init__(self):
        self.persisted = []

    async def _persist_parsed_full_docs(self, doc_id, payload):
        self.persisted.append((doc_id, payload))

    def _resolve_source_file_for_parser(
        self, file_path, *, source_file=None, parser_engine=None
    ):
        return file_path


@pytest.fixture
def archived(monkeypatch):
    calls = []

    async def _record(source_path):
        calls.append(Path(source_path))
        return source_path

    monkeypatch.setattr(_pipeline, "archive_docx_source_after_full_docs_sync", _record)
    return calls


def _fake_convert(markdown=_MD, stats=None):
    def _convert(source, work_dir, **kw):
        work_dir.mkdir(parents=True, exist_ok=True)
        return ConversionResult(
            markdown=markdown,
            figure_dir=None,
            stats=stats or {"doc_type": "book", "pages": 2},
            stdout="",
        )

    return _convert


def _ctx(rag, source: Path, doc_id: str) -> ParseContext:
    return ParseContext(rag, doc_id, str(source), {"parse_engine": "pdf2md"})


async def test_parse_text_pdf_delegates_and_repoints(
    text_pdf: Path, archived, monkeypatch
):
    monkeypatch.setattr(p2m, "convert_source", _fake_convert())
    rag = _FakeRag()
    result = await Pdf2MdParser().parse(_ctx(rag, text_pdf, "doc-1"))

    bundle = text_pdf.with_suffix(".textpack")
    assert bundle.is_file()
    assert result.parse_engine == "pdf2md"
    assert result.parse_format == FULL_DOCS_FORMAT_ONTORAG
    assert result.canonical_source == "book.textpack"
    assert result.document_metadata["doc_type"] == "book"
    assert result.document_metadata["bibliographic"] == {"title": "T", "authors": ["A"]}
    assert result.document_metadata["ocr"] is None
    assert result.blocks_path.endswith(".blocks.jsonl")
    # the Markdown parser archived the bundle, not the PDF
    assert archived == [bundle]
    assert text_pdf.is_file()
    with zipfile.ZipFile(bundle) as z:
        assert "pdf2md.json" in z.namelist()
        assert z.read("book.md").decode().startswith("# T")


async def test_parse_image_only_pdf_runs_ocr_first(
    image_pdf: Path, archived, monkeypatch
):
    seen = {}

    def fake_ocr(source, **kw):
        seen["source"] = source
        make_text_pdf(source)  # simulate in-place replacement
        return p2m.OcrResult(
            applied=True,
            backup=source.parent / "__originals__" / source.name,
            engine="tesseract",
            languages="eng",
        )

    monkeypatch.setattr(p2m, "ocr_in_place", fake_ocr)
    monkeypatch.setattr(p2m, "convert_source", _fake_convert())
    monkeypatch.setattr(p2m, "check_ocr_available", lambda: None)
    result = await Pdf2MdParser().parse(_ctx(_FakeRag(), image_pdf, "doc-2"))
    assert seen["source"] == image_pdf
    assert result.document_metadata["ocr"]["applied"] is True
    assert (
        result.document_metadata["ocr"]["original_backup"] == "__originals__/scan.pdf"
    )


async def test_parse_image_only_pdf_without_ocr_prereqs_fails_clearly(
    image_pdf: Path, monkeypatch
):
    monkeypatch.setattr(
        p2m,
        "check_ocr_available",
        lambda: "OCR for scanned PDFs is unavailable; missing: tesseract",
    )
    with pytest.raises(ValueError, match="missing: tesseract"):
        await Pdf2MdParser().parse(_ctx(_FakeRag(), image_pdf, "doc-3"))


async def test_parse_reuses_existing_bundle_for_unchanged_source(
    text_pdf: Path, archived, monkeypatch
):
    calls = {"n": 0}

    def counting(source, work_dir, **kw):
        calls["n"] += 1
        return _fake_convert()(source, work_dir, **kw)

    monkeypatch.setattr(p2m, "convert_source", counting)
    ctx = _ctx(_FakeRag(), text_pdf, "doc-4")
    await Pdf2MdParser().parse(ctx)
    # The delegate archived (moved) the bundle in real life; the fake archive
    # leaves it in place, so a second parse must find and reuse it.
    await Pdf2MdParser().parse(ctx)
    assert calls["n"] == 1


async def test_conversion_error_is_surfaced(text_pdf: Path, monkeypatch):
    def boom(source, work_dir, **kw):
        raise Pdf2MdConversionError("no text layer")

    monkeypatch.setattr(p2m, "convert_source", boom)
    with pytest.raises(ValueError, match="no text layer"):
        await Pdf2MdParser().parse(_ctx(_FakeRag(), text_pdf, "doc-5"))


async def test_unsupported_suffix_rejected(tmp_path: Path):
    src = tmp_path / "notes.txt"
    src.write_text("x")
    with pytest.raises(ValueError, match="does not support"):
        await Pdf2MdParser().parse(_ctx(_FakeRag(), src, "doc-6"))


async def test_real_conversion_of_synthesised_pdf(text_pdf: Path, archived):
    result = await Pdf2MdParser().parse(_ctx(_FakeRag(), text_pdf, "doc-real"))
    assert result.canonical_source == "book.textpack"
    blocks = Path(result.blocks_path).read_text(encoding="utf-8")
    assert "Body text of the page" in blocks
