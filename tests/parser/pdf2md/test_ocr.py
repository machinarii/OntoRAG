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


# ---------------------------------------------------------------------------
# Hardening (paperless-ngx lessons): argument construction, sidecar text,
# exit-code policy with a single retry, skip on encrypted/signed PDFs.
# ---------------------------------------------------------------------------


def _argv_for(settings, **kw):
    return ocr_mod._build_ocrmypdf_args(
        Path("/in/a.pdf"), Path("/out/a.pdf"), settings, **kw
    )


def test_args_auto_mode_defaults():
    argv = _argv_for(ocr_mod.OcrSettings())
    assert "--skip-text" in argv and "--force-ocr" not in argv
    assert "--rotate-pages" in argv
    assert argv[argv.index("--rotate-pages-threshold") + 1] == "12"
    assert argv[argv.index("--output-type") + 1] == "pdf"
    assert "--clean" not in argv and "--clean-final" not in argv
    assert "--sidecar" not in argv


def test_args_force_and_redo_modes():
    assert "--force-ocr" in _argv_for(ocr_mod.OcrSettings(mode="force"))
    redo = _argv_for(ocr_mod.OcrSettings(mode="redo", deskew=True))
    assert "--redo-ocr" in redo
    assert "--deskew" not in redo  # OCRmyPDF forbids deskew with redo


def test_args_clean_output_type_pixels_dpi_user_args_sidecar():
    settings = ocr_mod.OcrSettings(
        clean="clean-final",
        output_type="pdfa-2",
        max_image_mpixels=128.5,
        image_dpi=300,
        user_args=("--tesseract-timeout", "120"),
        sidecar_path=Path("/out/a.txt"),
    )
    argv = _argv_for(settings)
    assert "--clean-final" in argv
    assert argv[argv.index("--output-type") + 1] == "pdfa-2"
    assert argv[argv.index("--max-image-mpixels") + 1] == "128.5"
    assert argv[argv.index("--image-dpi") + 1] == "300"
    assert argv[argv.index("--tesseract-timeout") + 1] == "120"
    assert argv[argv.index("--sidecar") + 1] == "/out/a.txt"
    assert argv[-2:] == ["/in/a.pdf", "/out/a.pdf"]


def test_args_clean_final_degrades_to_clean_in_redo_mode():
    argv = _argv_for(ocr_mod.OcrSettings(mode="redo", clean="clean-final"))
    assert "--clean" in argv and "--clean-final" not in argv


def test_user_args_parse_json_or_shell():
    assert ocr_mod.parse_user_args('["--a", "1"]') == ("--a", "1")
    assert ocr_mod.parse_user_args("--b 2 --c") == ("--b", "2", "--c")
    assert ocr_mod.parse_user_args("") == ()
    assert ocr_mod.parse_user_args(None) == ()


def test_command_error_carries_exit_code(monkeypatch, tmp_path: Path):
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 6
            stderr = "PriorOcrFoundError: page already has text!\n"

        return R()

    monkeypatch.setattr(ocr_mod.subprocess, "run", fake_run)
    with pytest.raises(ocr_mod.OcrCommandError) as info:
        ocr_mod._run_ocrmypdf(
            tmp_path / "in.pdf", tmp_path / "out.pdf", ocr_mod.OcrSettings()
        )
    assert info.value.exit_code == 6
    assert "PriorOcrFoundError" in info.value.stderr_tail


def _scripted_runner(script):
    """``script``: list of callables run in order; each gets (source, output, settings)."""
    calls: list[ocr_mod.OcrSettings] = []

    def runner(source, output, settings):
        calls.append(settings)
        return script[len(calls) - 1](source, output, settings)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _raise(code, stderr=""):
    def _r(source, output, settings):
        raise ocr_mod.OcrCommandError(code, stderr)

    return _r


def _write_text_pdf_and_sidecar(source, output, settings):
    make_text_pdf(output)
    if settings.sidecar_path is not None:
        settings.sidecar_path.write_text("recognised words", encoding="utf-8")


def test_prior_ocr_found_in_auto_mode_retries_with_force(image_pdf: Path):
    runner = _scripted_runner(
        [_raise(6, "PriorOcrFoundError"), _write_text_pdf_and_sidecar]
    )
    result = ocr_in_place(image_pdf, runner=runner)
    assert [s.mode for s in runner.calls] == ["auto", "force"]
    assert result.retried is True and result.mode_used == "force"
    assert result.sidecar_chars == len("recognised words")


def test_prior_ocr_found_in_force_mode_is_not_retried(image_pdf: Path):
    runner = _scripted_runner([_raise(6, "PriorOcrFoundError")])
    with pytest.raises(OcrError):
        ocr_in_place(image_pdf, mode="force", runner=runner)
    assert len(runner.calls) == 1


def test_encrypted_pdf_is_skipped_not_failed(image_pdf: Path):
    original = image_pdf.read_bytes()
    runner = _scripted_runner([_raise(8, "EncryptedPdfError")])
    with pytest.raises(ocr_mod.OcrSkippedError, match="encrypted"):
        ocr_in_place(image_pdf, runner=runner)
    assert image_pdf.read_bytes() == original
    assert not list(image_pdf.parent.glob(".scan.pdf.ocr-*"))


def test_digitally_signed_pdf_is_skipped(image_pdf: Path):
    runner = _scripted_runner(
        [_raise(2, "DigitalSignatureError: Input PDF has a digital signature")]
    )
    with pytest.raises(ocr_mod.OcrSkippedError, match="signed"):
        ocr_in_place(image_pdf, runner=runner)


def test_other_input_file_error_is_a_failure(image_pdf: Path):
    runner = _scripted_runner([_raise(2, "InputFileError: not a PDF")])
    with pytest.raises(OcrError, match="not a PDF"):
        ocr_in_place(image_pdf, runner=runner)


def test_pdfa_conversion_failure_retries_with_plain_pdf(image_pdf: Path):
    runner = _scripted_runner(
        [_raise(10, "pdfa conversion failed"), _write_text_pdf_and_sidecar]
    )
    result = ocr_in_place(image_pdf, output_type="pdfa", runner=runner)
    assert [s.output_type for s in runner.calls] == ["pdfa", "pdf"]
    assert result.output_type == "pdf" and result.retried is True


def test_missing_dependency_names_the_tool(image_pdf: Path):
    runner = _scripted_runner(
        [_raise(3, "The program 'unpaper' could not be executed")]
    )
    with pytest.raises(OcrError, match="unpaper"):
        ocr_in_place(image_pdf, clean="clean", runner=runner)


def test_sidecar_text_is_the_primary_text_check(image_pdf: Path):
    """Sidecar says text was recognised even though the census would not
    find it (fake output has no text layer) -> accepted."""

    def runner(source, output, settings):
        make_image_only_pdf(output)
        settings.sidecar_path.write_text("some words", encoding="utf-8")

    result = ocr_in_place(image_pdf, runner=runner)
    assert result.sidecar_chars == len("some words")
    assert not list(image_pdf.parent.glob("*.txt"))  # sidecar cleaned up
