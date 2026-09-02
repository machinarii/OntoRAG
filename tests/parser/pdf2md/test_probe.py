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
