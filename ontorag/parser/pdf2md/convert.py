"""Library entry point over the vendored pdf2md tool.

``convert_source`` builds the argparse namespace upstream ``main()`` would
have produced and calls ``_pdf2md.run``. Upstream reports refusals with
``sys.exit(message)`` and progress with ``print``; both are contained here
so callers see a normal exception and a captured log.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Pdf2MdConversionError(RuntimeError):
    """pdf2md refused or failed to convert the source."""


@dataclass
class ConversionResult:
    markdown: str
    figure_dir: Path | None
    stats: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""


def _namespace(
    source: Path,
    out_md: Path,
    figure_dir: Path,
    artifacts: Path,
    *,
    doc_type: str | None,
    soffice: str | None,
) -> argparse.Namespace:
    # Mirrors every add_argument in _pdf2md.main(); defaults are upstream's.
    return argparse.Namespace(
        input=str(source),
        output=str(out_md),
        pages="",
        profile=False,
        glyph_report=False,
        emit_json=None,
        no_toc=False,
        math_delims=False,
        figure_dir=str(figure_dir),
        figure_vlm=None,  # OntoRAG's VLM role captions figures; never pdf2md's
        artifacts=str(artifacts),
        title=None,
        author=[],
        soffice=soffice,
        doc_type=doc_type,
        body_only=False,
    )


def convert_source(
    source: Path,
    work_dir: Path,
    *,
    doc_type: str | None = None,
    soffice: str | None = None,
    figure_dpi: int = 200,
) -> ConversionResult:
    source = Path(source)
    if not source.is_file():
        raise Pdf2MdConversionError(f"not found: {source}")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_md = work_dir / f"{source.stem}.md"
    figure_dir = work_dir / "figs"
    artifacts = work_dir / "artifacts"

    from ontorag.parser.pdf2md import _pdf2md  # heavy: PyMuPDF

    args = _namespace(
        source, out_md, figure_dir, artifacts, doc_type=doc_type, soffice=soffice
    )
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            if hasattr(_pdf2md, "render_region"):
                # Figure crop resolution: upstream hardcodes dpi=200 as a
                # default argument; adjust it rather than editing the vendored
                # source.
                _pdf2md.render_region.__defaults__ = (figure_dpi, 18.0)
            _pdf2md.run(args)
    except SystemExit as exc:  # upstream's refusal channel
        raise Pdf2MdConversionError(str(exc.code)) from exc
    except Exception as exc:  # noqa: BLE001 - any upstream failure is a conversion failure
        raise Pdf2MdConversionError(
            f"{source.name}: {type(exc).__name__}: {exc}"
        ) from exc

    if not out_md.is_file():
        raise Pdf2MdConversionError(f"{source.name}: pdf2md produced no Markdown")
    stats: dict[str, Any] = {}
    stats_path = artifacts / "stats.json"
    if stats_path.is_file():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats = {}
    return ConversionResult(
        markdown=out_md.read_text(encoding="utf-8"),
        figure_dir=figure_dir if figure_dir.is_dir() else None,
        stats=stats,
        stdout=buf.getvalue(),
    )
