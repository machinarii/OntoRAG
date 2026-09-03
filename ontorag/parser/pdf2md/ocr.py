"""OCR a scanned PDF in place: back up the original, replace it with a
searchable PDF produced by OCRmyPDF.

Why a subprocess: ``python -m ocrmypdf`` gives a hard timeout and keeps
OCRmyPDF's multiprocessing out of the parse worker. The recognizer is
OCRmyPDF's choice (``--ocr-engine``; plugins such as ocrmypdf-appleocr /
-easyocr / -paddleocr register themselves), so OntoRAG stays backend-agnostic.

The argument construction and the exit-code policy follow what paperless-ngx
learned over years of scanner input (design borrowed, no code):

* ``mode``: ``auto`` (``--skip-text``), ``force`` (``--force-ocr``) or
  ``redo`` (``--redo-ocr``; ``--deskew`` is dropped because OCRmyPDF forbids
  the pair).
* one retry at most: a text layer found in ``auto`` mode (exit 6) retries
  with ``force``; a failed PDF/A conversion (exit 10) retries with plain
  ``pdf`` output.
* encrypted (exit 8) and digitally signed (exit 2 + signature message) PDFs
  are *skipped*, never failed: OCR would be refused or would invalidate the
  signature, and the source must stay untouched.
* a ``--sidecar`` text file is always requested; its non-blank length is the
  primary proof that OCR produced text (the page census is the fallback).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ontorag.parser.pdf2md.census import pdf_text_layer_census
from ontorag.utils import logger

OCR_MODES = ("auto", "force", "redo")
OCR_CLEAN_MODES = ("none", "clean", "clean-final")
OCR_OUTPUT_TYPES = ("pdf", "pdfa", "pdfa-1", "pdfa-2", "pdfa-3")

# ocrmypdf.exceptions.ExitCode values (stable across 14.x-17.x).
EXIT_INPUT_FILE = 2
EXIT_MISSING_DEPENDENCY = 3
EXIT_ALREADY_DONE_OCR = 6
EXIT_ENCRYPTED_PDF = 8
EXIT_PDFA_CONVERSION_FAILED = 10

_SIGNATURE_RE = re.compile(r"digital.?signature", re.I)
_MISSING_TOOL_RE = re.compile(
    r"program '([^']+)'|command '([^']+)'|(unpaper|tesseract|gs|ghostscript)", re.I
)


class OcrError(RuntimeError):
    """OCRmyPDF failed; the source file is untouched."""


class OcrProducedNoTextError(OcrError):
    """OCR ran but the output still has no text layer."""


class OcrSkippedError(OcrError):
    """OCR was deliberately not applied (encrypted or signed PDF); the source
    is untouched and the caller decides what that means for the document."""


class OcrCommandError(OcrError):
    """``ocrmypdf`` exited non-zero; carries the exit code for the policy."""

    def __init__(self, exit_code: int, stderr_tail: str = "") -> None:
        self.exit_code = int(exit_code)
        self.stderr_tail = stderr_tail
        super().__init__(
            f"ocrmypdf exited {self.exit_code}: {stderr_tail or 'no stderr'}"
        )


@dataclass(frozen=True)
class OcrSettings:
    engine: str = "tesseract"
    languages: str = "eng"
    deskew: bool = True
    timeout: int = 1800
    mode: str = "auto"
    rotate_pages: bool = True
    rotate_pages_threshold: int = 12
    clean: str = "none"
    output_type: str = "pdf"
    max_image_mpixels: float | None = None
    image_dpi: int | None = None
    user_args: tuple[str, ...] = ()
    # Set per run by ocr_in_place; a runner writes the recognised text here.
    sidecar_path: Path | None = None


@dataclass(frozen=True)
class OcrResult:
    applied: bool
    backup: Path
    engine: str
    languages: str
    mode_used: str = "auto"
    output_type: str = "pdf"
    retried: bool = False
    sidecar_chars: int = 0


def parse_user_args(raw: str | None) -> tuple[str, ...]:
    """``PDF_OCR_USER_ARGS``: a JSON array of strings, or a shell-split string."""
    raw = (raw or "").strip()
    if not raw:
        return ()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"PDF_OCR_USER_ARGS is not a JSON array: {exc}") from exc
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise ValueError("PDF_OCR_USER_ARGS must be a JSON array of strings")
        return tuple(parsed)
    return tuple(shlex.split(raw))


def _build_ocrmypdf_args(
    source: Path, output: Path, settings: OcrSettings
) -> list[str]:
    cmd = [sys.executable, "-m", "ocrmypdf"]
    if settings.mode == "force":
        cmd.append("--force-ocr")
    elif settings.mode == "redo":
        cmd.append("--redo-ocr")
    else:
        cmd.append("--skip-text")
    langs = "+".join(p.strip() for p in settings.languages.split(",") if p.strip())
    cmd += ["-l", langs or "eng"]
    if settings.deskew and settings.mode != "redo":
        cmd.append("--deskew")
    if settings.rotate_pages:
        cmd += [
            "--rotate-pages",
            "--rotate-pages-threshold",
            str(settings.rotate_pages_threshold),
        ]
    if settings.clean == "clean-final":
        # --clean-final is incompatible with --redo-ocr; degrade to --clean.
        cmd.append("--clean" if settings.mode == "redo" else "--clean-final")
    elif settings.clean == "clean":
        cmd.append("--clean")
    cmd += ["--output-type", settings.output_type]
    if settings.max_image_mpixels is not None:
        cmd += ["--max-image-mpixels", str(settings.max_image_mpixels)]
    if settings.image_dpi is not None:
        cmd += ["--image-dpi", str(settings.image_dpi)]
    if settings.sidecar_path is not None:
        cmd += ["--sidecar", str(settings.sidecar_path)]
    if settings.engine and settings.engine != "tesseract":
        cmd += ["--ocr-engine", settings.engine]
    cmd.extend(settings.user_args)
    cmd += [str(source), str(output)]
    return cmd


def _run_ocrmypdf(source: Path, output: Path, settings: OcrSettings) -> None:
    cmd = _build_ocrmypdf_args(source, output, settings)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=settings.timeout, check=False
    )
    if proc.returncode != 0:
        tail = " | ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise OcrCommandError(proc.returncode, tail)


def _missing_tool(stderr_tail: str) -> str:
    m = _MISSING_TOOL_RE.search(stderr_tail)
    if not m:
        return "a required tool"
    return next(g for g in m.groups() if g)


def _cleanup(*paths: Path | None) -> None:
    for p in paths:
        if p is not None:
            p.unlink(missing_ok=True)


def ocr_in_place(
    source: Path,
    *,
    originals_dirname: str = "__originals__",
    engine: str = "tesseract",
    languages: str = "eng",
    deskew: bool = True,
    timeout: int = 1800,
    mode: str = "auto",
    rotate_pages: bool = True,
    rotate_pages_threshold: int = 12,
    clean: str = "none",
    output_type: str = "pdf",
    max_image_mpixels: float | None = None,
    image_dpi: int | None = None,
    user_args: tuple[str, ...] = (),
    runner: Callable[[Path, Path, OcrSettings], None] | None = None,
) -> OcrResult:
    source = Path(source)
    backup_dir = source.parent / originals_dirname
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / source.name
    if backup.exists():
        logger.info("[pdf2md] original backup already present, keeping it: %s", backup)
    else:
        shutil.copy2(source, backup)

    tmp = source.parent / f".{source.name}.ocr-{os.getpid()}.tmp"
    sidecar = source.parent / f".{source.name}.ocr-{os.getpid()}.txt"
    settings = OcrSettings(
        engine=engine,
        languages=languages,
        deskew=deskew,
        timeout=timeout,
        mode=mode,
        rotate_pages=rotate_pages,
        rotate_pages_threshold=rotate_pages_threshold,
        clean=clean,
        output_type=output_type,
        max_image_mpixels=max_image_mpixels,
        image_dpi=image_dpi,
        user_args=tuple(user_args),
        sidecar_path=sidecar,
    )
    run = runner or _run_ocrmypdf
    retried = False

    def _attempt(current: OcrSettings) -> OcrSettings:
        """Run once; on a retryable exit, return the settings for the retry."""
        nonlocal retried
        try:
            run(source, tmp, current)
            return current
        except OcrCommandError as exc:
            code, tail = exc.exit_code, exc.stderr_tail
            if code == EXIT_ENCRYPTED_PDF:
                raise OcrSkippedError(
                    f"{source.name}: encrypted PDF; OCR not applied"
                ) from exc
            if code == EXIT_INPUT_FILE and _SIGNATURE_RE.search(tail):
                raise OcrSkippedError(
                    f"{source.name}: digitally signed PDF; OCR would invalidate the signature"
                ) from exc
            if code == EXIT_MISSING_DEPENDENCY:
                raise OcrError(
                    f"{source.name}: OCRmyPDF is missing {_missing_tool(tail)} "
                    f"(install it or change PDF_OCR_* settings): {tail}"
                ) from exc
            if not retried:
                if code == EXIT_ALREADY_DONE_OCR and current.mode == "auto":
                    retried = True
                    logger.info(
                        "[pdf2md] %s: text layer reported by ocrmypdf; retrying with force",
                        source.name,
                    )
                    return _attempt(dataclasses.replace(current, mode="force"))
                if code == EXIT_PDFA_CONVERSION_FAILED and current.output_type != "pdf":
                    retried = True
                    logger.info(
                        "[pdf2md] %s: PDF/A conversion failed; retrying with plain pdf",
                        source.name,
                    )
                    return _attempt(dataclasses.replace(current, output_type="pdf"))
            raise OcrError(f"{source.name}: {exc}") from exc

    try:
        used = _attempt(settings)
        if not tmp.is_file():
            raise OcrError(f"{source.name}: ocrmypdf produced no output file")
        sidecar_chars = 0
        if sidecar.is_file():
            try:
                sidecar_chars = len(
                    sidecar.read_text(encoding="utf-8", errors="replace").strip()
                )
            except OSError:
                sidecar_chars = 0
        if sidecar_chars == 0 and pdf_text_layer_census(tmp).image_only:
            raise OcrProducedNoTextError(f"{source.name}: OCR produced no text layer")
        os.replace(tmp, source)  # atomic on the same filesystem
    except OcrError:
        _cleanup(tmp, sidecar)
        raise
    except subprocess.TimeoutExpired as exc:
        _cleanup(tmp, sidecar)
        raise OcrError(
            f"{source.name}: OCR timed out after {settings.timeout}s"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _cleanup(tmp, sidecar)
        raise OcrError(f"{source.name}: {exc}") from exc
    _cleanup(sidecar)
    return OcrResult(
        applied=True,
        backup=backup,
        engine=used.engine,
        languages=used.languages,
        mode_used=used.mode,
        output_type=used.output_type,
        retried=retried,
        sidecar_chars=sidecar_chars,
    )
