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
    import ontorag.parser.pdf2md.convert as adapter

    real_run = adapter.subprocess.run
    seen = []

    def spy(command, **kwargs):
        seen.extend(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(adapter.subprocess, "run", spy)
    convert_source(text_pdf, tmp_path / "work", figure_dpi=144)
    assert "--figure-vlm" not in seen
    assert seen[3] == "144"
    assert seen[seen.index("--ocr") + 1] == "off"


def test_convert_missing_source_raises(tmp_path: Path):
    with pytest.raises(Pdf2MdConversionError, match="not found"):
        convert_source(tmp_path / "nope.pdf", tmp_path / "work")


def test_missing_reference_is_actionable(text_pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2MD_REPO_PATH", str(tmp_path / "missing"))
    with pytest.raises(Pdf2MdConversionError, match="PDF2MD_REPO_PATH"):
        convert_source(text_pdf, tmp_path / "work")


@pytest.mark.parametrize("failure", ["timeout", "no_output", "exit"])
def test_worker_failures(text_pdf, tmp_path, monkeypatch, failure):
    import subprocess
    import ontorag.parser.pdf2md.convert as adapter

    def fake_run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 600)
        return subprocess.CompletedProcess(
            command, 1 if failure == "exit" else 0, stdout="", stderr="upstream refused"
        )

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    with pytest.raises(Pdf2MdConversionError):
        convert_source(text_pdf, tmp_path / "work")


def test_external_checkout_path_and_worker_dpi(text_pdf, tmp_path, monkeypatch):
    """A separately installed checkout works without shell quoting or in-process imports."""
    reference = tmp_path / "separate checkout"
    reference.mkdir()
    (reference / "structured.py").write_text("DPI_OFFSET = 0\n")
    (reference / "pdf2md_all.py").write_text(
        "import argparse, structured\n"
        "from pathlib import Path\n"
        "def render_region(doc, region, out_dir, dpi=200, margin=18.0):\n"
        "    return dpi + structured.DPI_OFFSET\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--output')\n"
        "    args, rest = parser.parse_known_args()\n"
        "    Path(args.output).write_text(str(render_region(None, None, None)))\n"
    )
    monkeypatch.setenv("PDF2MD_REPO_PATH", str(reference))
    result = convert_source(text_pdf, tmp_path / "work with spaces", figure_dpi=144)
    assert result.markdown == "144"
    result = convert_source(text_pdf, tmp_path / "second", figure_dpi=300)
    assert result.markdown == "300"
    assert "dpi=200" in (reference / "pdf2md_all.py").read_text()
