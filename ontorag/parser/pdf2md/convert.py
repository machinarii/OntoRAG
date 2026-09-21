"""Thin subprocess adapter for the referenced machinarii/pdf2md checkout."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .reference import reference_available, repository_path


class Pdf2MdConversionError(RuntimeError):
    """pdf2md refused or failed to convert the source."""


@dataclass
class ConversionResult:
    markdown: str
    figure_dir: Path | None
    stats: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""


def convert_source(
    source: Path,
    work_dir: Path,
    *,
    doc_type: str | None = None,
    soffice: str | None = None,
    figure_dpi: int = 200,
) -> ConversionResult:
    source = Path(source).resolve()
    if not source.is_file():
        raise Pdf2MdConversionError(f"not found: {source}")
    if not reference_available():
        raise Pdf2MdConversionError(
            "pdf2md checkout is missing; run git submodule update --init external/pdf2md "
            "or set PDF2MD_REPO_PATH to a machinarii/pdf2md checkout"
        )
    if figure_dpi <= 0:
        raise Pdf2MdConversionError("PDF2MD_FIGURE_DPI must be positive")
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_md = work_dir / f"{source.stem}.md"
    figure_dir = work_dir / "figs"
    artifacts = work_dir / "artifacts"
    command = [
        sys.executable,
        str(Path(__file__).with_name("_worker.py")),
        str(repository_path()),
        str(figure_dpi),
        str(source),
        "--output",
        str(out_md),
        "--figure-dir",
        str(figure_dir),
        "--artifacts",
        str(artifacts),
        "--ocr",
        "off",
    ]
    # OntoRAG owns OCR and VLM analysis; never pass --figure-vlm.
    if doc_type is not None:
        command.extend(["--doc-type", doc_type])
    if soffice is not None:
        command.extend(["--soffice", soffice])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Pdf2MdConversionError(f"{source.name}: {exc}") from exc
    if result.returncode:
        raise Pdf2MdConversionError(
            f"{source.name}: {result.stderr.strip() or result.stdout.strip() or 'pdf2md failed'}"
        )
    if not out_md.is_file():
        raise Pdf2MdConversionError(f"{source.name}: pdf2md produced no Markdown")
    stats: dict[str, Any] = {}
    stats_path = artifacts / "stats.json"
    if stats_path.is_file():
        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                stats = data
        except json.JSONDecodeError:
            pass
    return ConversionResult(
        markdown=out_md.read_text(encoding="utf-8"),
        figure_dir=figure_dir if figure_dir.is_dir() else None,
        stats=stats,
        stdout=result.stdout,
    )
