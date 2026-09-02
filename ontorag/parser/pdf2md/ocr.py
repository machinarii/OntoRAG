"""OCR a scanned PDF in place: back up the original, replace it with a
searchable PDF produced by OCRmyPDF.

Why a subprocess: ``python -m ocrmypdf`` gives a hard timeout and keeps
OCRmyPDF's multiprocessing out of the parse worker. The recognizer is
OCRmyPDF's choice (``--ocr-engine``; plugins such as ocrmypdf-appleocr /
-easyocr / -paddleocr register themselves), so OntoRAG stays backend-agnostic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ontorag.parser.pdf2md.census import pdf_text_layer_census
from ontorag.utils import logger


class OcrError(RuntimeError):
    """OCRmyPDF failed; the source file is untouched."""


class OcrProducedNoTextError(OcrError):
    """OCR ran but the output still has no text layer."""


@dataclass(frozen=True)
class OcrSettings:
    engine: str = "tesseract"
    languages: str = "eng"
    deskew: bool = True
    timeout: int = 1800


@dataclass(frozen=True)
class OcrResult:
    applied: bool
    backup: Path
    engine: str
    languages: str


def _run_ocrmypdf(source: Path, output: Path, settings: OcrSettings) -> None:
    langs = "+".join(p.strip() for p in settings.languages.split(",") if p.strip())
    cmd = [sys.executable, "-m", "ocrmypdf", "--skip-text", "-l", langs]
    if settings.deskew:
        cmd.append("--deskew")
    if settings.engine and settings.engine != "tesseract":
        cmd += ["--ocr-engine", settings.engine]
    cmd += [str(source), str(output)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=settings.timeout, check=False
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise OcrError(
            f"ocrmypdf exited {proc.returncode}: {' | '.join(tail) or 'no stderr'}"
        )


def ocr_in_place(
    source: Path,
    *,
    originals_dirname: str = "__originals__",
    engine: str = "tesseract",
    languages: str = "eng",
    deskew: bool = True,
    timeout: int = 1800,
    runner: Callable[[Path, Path, OcrSettings], None] | None = None,
) -> OcrResult:
    source = Path(source)
    settings = OcrSettings(
        engine=engine, languages=languages, deskew=deskew, timeout=timeout
    )
    backup_dir = source.parent / originals_dirname
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / source.name
    if backup.exists():
        logger.info("[pdf2md] original backup already present, keeping it: %s", backup)
    else:
        shutil.copy2(source, backup)

    tmp = source.parent / f".{source.name}.ocr-{os.getpid()}.tmp"
    try:
        (runner or _run_ocrmypdf)(source, tmp, settings)
        if not tmp.is_file():
            raise OcrError("ocrmypdf produced no output file")
        if pdf_text_layer_census(tmp).image_only:
            raise OcrProducedNoTextError(f"{source.name}: OCR produced no text layer")
        os.replace(tmp, source)  # atomic on the same filesystem
    except OcrError:
        tmp.unlink(missing_ok=True)
        raise
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise OcrError(
            f"{source.name}: OCR timed out after {settings.timeout}s"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise OcrError(f"{source.name}: {exc}") from exc
    return OcrResult(
        applied=True,
        backup=backup,
        engine=settings.engine,
        languages=settings.languages,
    )
