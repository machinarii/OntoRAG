"""Import-cheap availability probes and startup checks for the pdf2md engine.

Stdlib only. The OCR checks mirror what paperless-ngx validates at startup
(design borrowed, no code): binaries for the *configured* settings, enum
values, and the Tesseract language packs actually installed — so a
misconfiguration is reported once at boot, not once per document.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps this module stdlib-cheap
    from ontorag.parser.pdf2md.ocr import OcrSettings

INSTALL_HINT = "pip install 'ontorag[pdf2md]'"


def check_pdf2md_available() -> bool:
    return importlib.util.find_spec("pymupdf") is not None


def check_ocr_available(settings: "OcrSettings | None" = None) -> str | None:
    """None when every tool the configured OCR settings need is present."""
    missing = []
    if importlib.util.find_spec("ocrmypdf") is None:
        missing.append(f"ocrmypdf ({INSTALL_HINT})")
    binaries = [("tesseract", "tesseract-ocr"), ("gs", "ghostscript")]
    if settings is not None and getattr(settings, "clean", "none") != "none":
        binaries.append(("unpaper", "unpaper (needed by PDF_OCR_CLEAN)"))
    for binary, hint in binaries:
        if shutil.which(binary) is None:
            missing.append(f"{binary} (system package {hint})")
    if not missing:
        return None
    return "OCR for scanned PDFs is unavailable; missing: " + ", ".join(missing)


def validate_ocr_settings(settings: "OcrSettings") -> list[str]:
    """Human-readable problems with enum-valued OCR settings (empty = fine)."""
    from ontorag.parser.pdf2md.ocr import OCR_CLEAN_MODES, OCR_MODES, OCR_OUTPUT_TYPES

    problems: list[str] = []
    checks = (
        ("PDF_OCR_MODE", settings.mode, OCR_MODES),
        ("PDF_OCR_CLEAN", settings.clean, OCR_CLEAN_MODES),
        ("PDF_OCR_OUTPUT_TYPE", settings.output_type, OCR_OUTPUT_TYPES),
    )
    for name, value, allowed in checks:
        if value not in allowed:
            problems.append(f"{name}={value!r} is not one of {', '.join(allowed)}")
    if settings.rotate_pages_threshold < 1:
        problems.append("PDF_OCR_ROTATE_PAGES_THRESHOLD must be >= 1")
    if settings.timeout <= 0:
        problems.append("PDF_OCR_TIMEOUT must be a positive number of seconds")
    return problems


def installed_tesseract_languages() -> set[str] | None:
    """Language packs Tesseract reports, or None when it cannot be asked."""
    if shutil.which("tesseract") is None:
        return None
    try:
        proc = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    langs: set[str] = set()
    for line in (proc.stdout or "").splitlines() + (proc.stderr or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("list of"):
            continue
        langs.add(line)
    return langs


def check_tesseract_languages(languages: str) -> str | None:
    """None when every configured language pack is installed (or Tesseract
    cannot be asked — the binary probe reports that case)."""
    installed = installed_tesseract_languages()
    if installed is None:
        return None
    wanted = [
        p.strip() for p in (languages or "").replace("+", ",").split(",") if p.strip()
    ]
    missing = [lang for lang in wanted if lang not in installed]
    if not missing:
        return None
    return (
        f"Tesseract language pack(s) not installed: {', '.join(missing)} "
        f"(PDF_OCR_LANGUAGES={languages!r}; installed: {', '.join(sorted(installed)) or 'none'})"
    )


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
