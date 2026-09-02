from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from ontorag.parser.pdf2md import ocr as ocr_mod  # noqa: E402
from ontorag.parser.pdf2md.ocr import (  # noqa: E402
    OcrError,
    OcrProducedNoTextError,
    ocr_in_place,
)
from tests.parser.pdf2md.conftest import make_image_only_pdf, make_text_pdf  # noqa: E402

pytestmark = pytest.mark.offline


def _fake_runner_writes_text_pdf(source: Path, output: Path, settings) -> None:
    make_text_pdf(output)


def test_ocr_backs_up_original_and_replaces_in_place(image_pdf: Path):
    original_bytes = image_pdf.read_bytes()
    result = ocr_in_place(image_pdf, runner=_fake_runner_writes_text_pdf)
    assert result.applied is True
    assert result.backup == image_pdf.parent / "__originals__" / "scan.pdf"
    assert result.backup.read_bytes() == original_bytes
    assert image_pdf.read_bytes() != original_bytes  # now the OCR'd file
    assert not list(image_pdf.parent.glob(".scan.pdf.ocr-*"))  # temp cleaned


def test_ocr_never_overwrites_existing_backup(image_pdf: Path):
    backup_dir = image_pdf.parent / "__originals__"
    backup_dir.mkdir()
    (backup_dir / "scan.pdf").write_bytes(b"first backup wins")
    ocr_in_place(image_pdf, runner=_fake_runner_writes_text_pdf)
    assert (backup_dir / "scan.pdf").read_bytes() == b"first backup wins"


def test_ocr_failure_leaves_source_untouched_and_no_temp(image_pdf: Path):
    original_bytes = image_pdf.read_bytes()

    def boom(source, output, settings):
        output.write_bytes(b"partial")
        raise RuntimeError("ocrmypdf exploded")

    with pytest.raises(OcrError, match="ocrmypdf exploded"):
        ocr_in_place(image_pdf, runner=boom)
    assert image_pdf.read_bytes() == original_bytes
    assert not list(image_pdf.parent.glob(".scan.pdf.ocr-*"))
    assert (image_pdf.parent / "__originals__" / "scan.pdf").exists()


def test_ocr_output_without_text_layer_is_rejected(image_pdf: Path):
    def still_images(source, output, settings):
        make_image_only_pdf(output)

    with pytest.raises(OcrProducedNoTextError):
        ocr_in_place(image_pdf, runner=still_images)
    assert not list(image_pdf.parent.glob(".scan.pdf.ocr-*"))


def test_default_runner_builds_ocrmypdf_command(monkeypatch, tmp_path: Path):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["timeout"] = kwargs.get("timeout")
        Path(cmd[-1]).write_bytes(b"out")

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(ocr_mod.subprocess, "run", fake_run)
    settings = ocr_mod.OcrSettings(
        engine="appleocr", languages="eng,deu", deskew=True, timeout=42
    )
    ocr_mod._run_ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", settings)
    cmd = calls["cmd"]
    assert cmd[1:3] == ["-m", "ocrmypdf"]
    assert "--skip-text" in cmd and "--deskew" in cmd
    assert cmd[cmd.index("-l") + 1] == "eng+deu"
    assert cmd[cmd.index("--ocr-engine") + 1] == "appleocr"
    assert calls["timeout"] == 42


def test_default_runner_omits_ocr_engine_flag_for_tesseract(
    monkeypatch, tmp_path: Path
):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(ocr_mod.subprocess, "run", fake_run)
    ocr_mod._run_ocrmypdf(
        tmp_path / "in.pdf", tmp_path / "out.pdf", ocr_mod.OcrSettings()
    )
    assert "--ocr-engine" not in calls["cmd"]


def test_default_runner_surfaces_nonzero_exit(monkeypatch, tmp_path: Path):
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 2
            stderr = "line1\nline2\nInputFileError: bad pdf\n"

        return R()

    monkeypatch.setattr(ocr_mod.subprocess, "run", fake_run)
    with pytest.raises(OcrError, match="exited 2.*bad pdf"):
        ocr_mod._run_ocrmypdf(
            tmp_path / "in.pdf", tmp_path / "out.pdf", ocr_mod.OcrSettings()
        )
