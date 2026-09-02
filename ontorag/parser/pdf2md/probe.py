"""Import-cheap availability probes for the pdf2md engine (stdlib only)."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

INSTALL_HINT = "pip install 'ontorag[pdf2md]'"


def check_pdf2md_available() -> bool:
    return importlib.util.find_spec("pymupdf") is not None


def check_ocr_available() -> str | None:
    missing = []
    if importlib.util.find_spec("ocrmypdf") is None:
        missing.append(f"ocrmypdf ({INSTALL_HINT})")
    for binary, hint in (("tesseract", "tesseract-ocr"), ("gs", "ghostscript")):
        if shutil.which(binary) is None:
            missing.append(f"{binary} (system package {hint})")
    if not missing:
        return None
    return "OCR for scanned PDFs is unavailable; missing: " + ", ".join(missing)


def check_soffice_available(explicit: str | None = None) -> str | None:
    if explicit:
        if Path(explicit).is_file():
            return None
        return f"PDF2MD_SOFFICE points at a missing file: {explicit}"
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return None
    return (
        "DOC/ODT/RTF conversion needs LibreOffice (soffice) on PATH or PDF2MD_SOFFICE"
    )
