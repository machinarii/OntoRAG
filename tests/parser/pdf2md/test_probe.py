import pytest

from ontorag.parser.pdf2md import probe

pytestmark = pytest.mark.offline


def test_pdf2md_available_tracks_pymupdf(monkeypatch):
    monkeypatch.setattr(probe.importlib.util, "find_spec", lambda name: None)
    assert probe.check_pdf2md_available() is False
    monkeypatch.setattr(probe.importlib.util, "find_spec", lambda name: object())
    assert probe.check_pdf2md_available() is True


def test_ocr_probe_names_each_missing_prerequisite(monkeypatch):
    monkeypatch.setattr(probe.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    msg = probe.check_ocr_available()
    assert msg is not None
    assert "ocrmypdf" in msg and "tesseract" in msg and "gs" in msg
    assert "ontorag[pdf2md]" in msg


def test_ocr_probe_ok_when_all_present(monkeypatch):
    monkeypatch.setattr(probe.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(probe.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert probe.check_ocr_available() is None


def test_soffice_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    assert "LibreOffice" in probe.check_soffice_available()
    exe = tmp_path / "soffice"
    exe.write_text("")
    exe.chmod(0o755)
    assert probe.check_soffice_available(str(exe)) is None
    assert "missing file" in probe.check_soffice_available(str(tmp_path / "nope"))


# ---------------------------------------------------------------------------
# Startup checks (paperless-ngx lessons): settings enums, unpaper, tesseract
# language packs.
# ---------------------------------------------------------------------------


def test_validate_ocr_settings_reports_each_bad_enum():
    from ontorag.parser.pdf2md.ocr import OcrSettings

    problems = probe.validate_ocr_settings(
        OcrSettings(mode="sometimes", clean="scrub", output_type="docx")
    )
    joined = " ".join(problems)
    assert "PDF_OCR_MODE" in joined and "sometimes" in joined
    assert "PDF_OCR_CLEAN" in joined and "scrub" in joined
    assert "PDF_OCR_OUTPUT_TYPE" in joined and "docx" in joined
    assert probe.validate_ocr_settings(OcrSettings()) == []


def test_ocr_probe_requires_unpaper_only_when_cleaning(monkeypatch):
    from ontorag.parser.pdf2md.ocr import OcrSettings

    monkeypatch.setattr(probe.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        probe.shutil,
        "which",
        lambda name: None if name == "unpaper" else f"/usr/bin/{name}",
    )
    assert probe.check_ocr_available(OcrSettings()) is None
    msg = probe.check_ocr_available(OcrSettings(clean="clean"))
    assert msg is not None and "unpaper" in msg


def test_tesseract_language_check(monkeypatch):
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = "List of available languages in ...:\neng\ndeu\nosd\n"
            stderr = ""

        return R()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/tesseract")
    assert probe.check_tesseract_languages("eng,deu") is None
    msg = probe.check_tesseract_languages("eng,fra")
    assert msg is not None
    missing_part = msg.split("(PDF_OCR_LANGUAGES")[0]
    assert "fra" in missing_part and "deu" not in missing_part
    assert "installed: deu, eng, osd" in msg  # operators see what IS there


def test_tesseract_language_check_is_silent_without_binary(monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda name: None)
    assert probe.check_tesseract_languages("eng") is None
