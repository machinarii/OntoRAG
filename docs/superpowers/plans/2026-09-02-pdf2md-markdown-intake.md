# pdf2md Markdown Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a `pdf2md` parser engine that converts PDF/EPUB/DOCX/DOC/ODT/RTF sources into a Markdown-canonical `.textpack` (OCR'ing scanned PDFs in place first), hands it to the native Markdown engine, and makes the `.textpack` the catalogued/archived document while the original stays untouched in its folder.

**Architecture:** A new in-tree engine `ontorag/parser/pdf2md/` runs in the parse worker (own queue group), orchestrating four pure, separately-tested units (text-layer census → OCR-in-place → vendored pdf2md conversion → `.textpack` packing) and then delegating the real parse to `NativeMarkdownParser` on the bundle. Two small pipeline contracts carry the identity change: `ParseResult.canonical_source` / `document_metadata` (applied in the parse stage) and a `CONVERTED_SOURCE` scan classification so re-scans skip converted originals.

**Tech Stack:** Python 3.10+, PyMuPDF (extra only), OCRmyPDF CLI via `python -m ocrmypdf` (extra only; Tesseract/Ghostscript system binaries), LibreOffice `soffice` (optional system binary), pytest, ruff, Bun/React for one WebUI cell.

**Spec:** `docs/superpowers/specs/2026-09-02-pdf2md-markdown-intake-design.md`

**Status:** all 11 tasks executed 2026-09-02 (inline, TDD); every step below is checked. Final verification: 7820 passed / 0 failed / 202 skipped; WebUI 616 pass; ruff clean.

## Global Constraints

- Engine name `pdf2md`; suffixes `{"pdf", "epub", "docx", "doc", "odt", "rtf"}`; `queue_group="pdf2md"`; concurrency env `MAX_PARALLEL_PARSE_PDF2MD` default `2`.
- Optional extra `pdf2md = ["pymupdf>=1.24", "ocrmypdf>=16"]`; PyMuPDF is AGPL-3.0 and must never be imported at module import time outside `ontorag/parser/pdf2md/` (registry stays import-cheap).
- `docx` stays routed to `native` in every shipped example; `pdf2md` for `docx` is opt-in only.
- Originals directory name `__originals__` (env `PDF2MD_ORIGINALS_DIRNAME`), created beside the source; an existing backup is never overwritten.
- OntoRAG never moves or deletes the original PDF/EPUB/…; only the `.textpack` is archived to `__parsed__/`.
- `.textpack` layout: `<stem>.md` at zip root (front matter removed), `figs/<sha256[:12]>.<ext>`, `pdf2md.json` (`"schema": 1`).
- Env vars: `PDF_OCR_ENGINE` (`tesseract`), `PDF_OCR_LANGUAGES` (`eng`), `PDF_OCR_DESKEW` (`true`), `PDF_OCR_TIMEOUT` (`1800`), `PDF2MD_ORIGINALS_DIRNAME` (`__originals__`), `PDF2MD_SOFFICE` (unset → PATH), `PDF2MD_FIGURE_DPI` (`200`).
- New `doc_status.metadata` keys: `source_file_original`, `bibliographic`, `doc_type`, `doc_scores`, `ocr`, `converter` — all added to `_DOC_STATUS_METADATA_CARRY_OVER_KEYS`.
- TDD for every unit; `ruff check .` and `ruff format` clean before each commit; commit messages end with the session's `Co-Authored-By` / `Claude-Session` trailers.
- Run tests with `./scripts/test.sh <path>` (it exports the macOS libcairo fallback path); `pytest.importorskip("pymupdf")` gates PyMuPDF tests.

---

## File map

| Path | Responsibility |
|---|---|
| `ontorag/parser/pdf2md/__init__.py` | Package marker; re-exports `Pdf2MdParser` lazily (no heavy imports). |
| `ontorag/parser/pdf2md/_pdf2md.py` | Vendored `pdf2md_all.py`, verbatim except `main()` split into `main()` + `run(args)`. |
| `ontorag/parser/pdf2md/README.pdf2md.md` | Vendored upstream README (provenance). |
| `ontorag/parser/pdf2md/convert.py` | `convert_source()` — library entry over the vendored tool; maps `SystemExit` to `Pdf2MdConversionError`. |
| `ontorag/parser/pdf2md/census.py` | `pdf_text_layer_census()` — PyMuPDF page/text census. |
| `ontorag/parser/pdf2md/ocr.py` | `ocr_in_place()` — backup → OCRmyPDF (subprocess) → verify text → atomic replace. |
| `ontorag/parser/pdf2md/textpack.py` | Front-matter parse/strip, figure dedupe + link rewrite, manifest, `pack_textpack()`. |
| `ontorag/parser/pdf2md/probe.py` | Import-cheap availability probes (pymupdf, ocrmypdf+tesseract+gs, soffice). |
| `ontorag/parser/pdf2md/parser.py` | `Pdf2MdParser` — orchestration + delegation to `NativeMarkdownParser`. |
| `ontorag/constants.py` | `PARSER_ENGINE_PDF2MD = "pdf2md"`. |
| `ontorag/parser/registry.py` | Built-in `ParserSpec` for `pdf2md`. |
| `ontorag/parser/base.py` | `ParseResult.canonical_source`, `ParseResult.document_metadata`. |
| `ontorag/pipeline.py` | Parse stage applies the two new fields. |
| `ontorag/utils_pipeline.py` | Carry-over keys. |
| `ontorag/api/routers/document_routes.py` | `_ScanFileClass.CONVERTED_SOURCE` + classifier + caller branch. |
| `ontorag_webui/src/features/DocumentManager.tsx` | Bibliographic title in the list row. |
| `pyproject.toml`, `env.example`, `env.docker-compose-full`, `Dockerfile` | Extra, env vars, system packages. |
| `tests/parser/pdf2md/…`, `tests/api/routes/test_scan_classification_exits.py`, `tests/pipeline/test_canonical_source.py` | Tests. |

---

### Task 1: Vendor pdf2md and expose `convert_source()`

**Files:**
- Create: `ontorag/parser/pdf2md/__init__.py`
- Create: `ontorag/parser/pdf2md/_pdf2md.py` (from `/Users/jin/Downloads/pdf2md-files.zip` → `pdf2md_all.py`)
- Create: `ontorag/parser/pdf2md/README.pdf2md.md` (from the zip's `README.md`)
- Create: `ontorag/parser/pdf2md/convert.py`
- Test: `tests/parser/pdf2md/__init__.py`, `tests/parser/pdf2md/conftest.py`, `tests/parser/pdf2md/test_convert.py`

**Interfaces:**
- Produces: `convert_source(source: Path, work_dir: Path, *, doc_type: str | None = None, soffice: str | None = None, figure_dpi: int = 200) -> ConversionResult` where `ConversionResult(markdown: str, figure_dir: Path | None, stats: dict[str, Any], stdout: str)`; raises `Pdf2MdConversionError(message)`.
- Produces: `_pdf2md.run(args: argparse.Namespace) -> None` (the refactored body of upstream `main()`).

- [x] **Step 1: Vendor the files**

```bash
mkdir -p ontorag/parser/pdf2md tests/parser/pdf2md
S=$(mktemp -d) && unzip -q /Users/jin/Downloads/pdf2md-files.zip -d "$S"
cp "$S/pdf2md_all.py" ontorag/parser/pdf2md/_pdf2md.py
cp "$S/README.md" ontorag/parser/pdf2md/README.pdf2md.md
touch tests/parser/pdf2md/__init__.py
```

Prepend this header to `ontorag/parser/pdf2md/_pdf2md.py` (before the module docstring's `#!/usr/bin/env python3` line, replacing it):

```python
# Vendored from the author's pdf2md single-file build (pdf2md-files.zip,
# 2026-09-02, 4360 lines). Only deliberate edit: ``main()`` is split into
# ``main()`` (argparse) + ``run(args)`` so ``convert.py`` can drive it as a
# library. Re-vendoring: copy the new build over this file and re-apply that
# one split. Everything else — including ``print`` and ``sys.exit`` — is
# upstream behaviour and is contained by ``convert.py``.
# ruff: noqa
```

- [x] **Step 2: Split `main()` into `main()` + `run(args)`**

In `_pdf2md.py`, find `def main():` (upstream line ~4255). Replace the two lines

```python
    args = ap.parse_args()

    src = Path(args.input)
```

with

```python
    args = ap.parse_args()
    run(args)


def run(args) -> None:
    """Body of ``main()`` after argument parsing (library entry point)."""
    src = Path(args.input)
```

Nothing else in the function body changes. `R = PP = OL = DT = DK = DD = ST = sys.modules[__name__]` at the bottom must stay.

- [x] **Step 3: Write the failing tests**

`tests/parser/pdf2md/conftest.py`:

```python
"""Shared fixtures: tiny PDFs synthesised with PyMuPDF (text page / image-only page)."""

from __future__ import annotations

from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")

# 1x1 white PNG
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02"
    b"\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


def make_text_pdf(path: Path, pages: int = 2, text: str = "Chapter 1: Arrival") -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} page {i + 1}", fontsize=18)
        page.insert_text((72, 120), "Body text of the page. " * 20, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def make_image_only_pdf(path: Path, pages: int = 2) -> Path:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(72, 72, 300, 300), stream=PNG_BYTES)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def text_pdf(tmp_path: Path) -> Path:
    return make_text_pdf(tmp_path / "book.pdf")


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    return make_image_only_pdf(tmp_path / "scan.pdf")
```

`tests/parser/pdf2md/test_convert.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from ontorag.parser.pdf2md.convert import (  # noqa: E402
    ConversionResult,
    Pdf2MdConversionError,
    convert_source,
)

pytestmark = pytest.mark.offline


def test_convert_text_pdf_yields_front_matter_and_body(text_pdf: Path, tmp_path: Path):
    result = convert_source(text_pdf, tmp_path / "work")
    assert isinstance(result, ConversionResult)
    assert result.markdown.startswith("---\n")
    assert "generator: pdf2md" in result.markdown
    assert "Body text of the page." in result.markdown
    assert result.stats["doc_type"] in {"book", "paper", "deck", "document"}


def test_convert_image_only_pdf_raises_conversion_error(image_pdf: Path, tmp_path: Path):
    with pytest.raises(Pdf2MdConversionError, match="image-only"):
        convert_source(image_pdf, tmp_path / "work")


def test_convert_never_passes_figure_vlm(text_pdf: Path, tmp_path: Path, monkeypatch):
    import ontorag.parser.pdf2md._pdf2md as vendored

    seen = {}
    real_run = vendored.run

    def spy(args):
        seen["figure_vlm"] = args.figure_vlm
        return real_run(args)

    monkeypatch.setattr(vendored, "run", spy)
    convert_source(text_pdf, tmp_path / "work")
    assert seen["figure_vlm"] is None


def test_convert_missing_source_raises(tmp_path: Path):
    with pytest.raises(Pdf2MdConversionError, match="not found"):
        convert_source(tmp_path / "nope.pdf", tmp_path / "work")
```

- [x] **Step 4: Run tests to verify they fail**

Run: `./scripts/test.sh tests/parser/pdf2md/test_convert.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ontorag.parser.pdf2md.convert'`

- [x] **Step 5: Implement `convert.py` and the package init**

`ontorag/parser/pdf2md/__init__.py`:

```python
"""pdf2md engine: PDF/EPUB/DOCX/DOC/ODT/RTF -> Markdown-canonical .textpack.

Import-cheap by design: the vendored converter (PyMuPDF, AGPL) is imported
only by ``convert.py`` when a document is actually converted. See
docs/superpowers/specs/2026-09-02-pdf2md-markdown-intake-design.md.
"""
```

`ontorag/parser/pdf2md/convert.py`:

```python
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


def _namespace(source: Path, out_md: Path, figure_dir: Path, artifacts: Path, *,
               doc_type: str | None, soffice: str | None) -> argparse.Namespace:
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

    args = _namespace(source, out_md, figure_dir, artifacts, doc_type=doc_type, soffice=soffice)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            if hasattr(_pdf2md, "render_region"):
                # figure crop resolution: upstream hardcodes dpi=200 as a default arg
                _pdf2md.render_region.__defaults__ = (figure_dpi, 18.0)
            _pdf2md.run(args)
    except SystemExit as exc:  # upstream's refusal channel
        raise Pdf2MdConversionError(str(exc.code)) from exc
    except Exception as exc:  # noqa: BLE001 - any upstream failure is a conversion failure
        raise Pdf2MdConversionError(f"{source.name}: {type(exc).__name__}: {exc}") from exc

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
```

- [x] **Step 6: Run tests to verify they pass**

Run: `./scripts/test.sh tests/parser/pdf2md/test_convert.py -v`
Expected: 4 PASS (or SKIP if `pymupdf` is not installed — install it first: `uv pip install pymupdf`).

- [x] **Step 7: Lint and commit**

```bash
.venv/bin/python -m ruff check ontorag/parser/pdf2md tests/parser/pdf2md && .venv/bin/python -m ruff format ontorag/parser/pdf2md/convert.py tests/parser/pdf2md
git add ontorag/parser/pdf2md tests/parser/pdf2md
git commit -m "feat(pdf2md): vendor pdf2md and expose convert_source()"
```

---

### Task 2: Text-layer census

**Files:**
- Create: `ontorag/parser/pdf2md/census.py`
- Test: `tests/parser/pdf2md/test_census.py`

**Interfaces:**
- Produces: `pdf_text_layer_census(path: Path) -> Census` with `Census(pages: int, text_pages: int)` and property `image_only -> bool` (`pages > 0 and text_pages == 0`).

- [x] **Step 1: Write the failing tests**

```python
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from ontorag.parser.pdf2md.census import Census, pdf_text_layer_census  # noqa: E402

pytestmark = pytest.mark.offline


def test_text_pdf_is_not_image_only(text_pdf: Path):
    census = pdf_text_layer_census(text_pdf)
    assert census == Census(pages=2, text_pages=2)
    assert census.image_only is False


def test_image_only_pdf_is_detected(image_pdf: Path):
    census = pdf_text_layer_census(image_pdf)
    assert census.pages == 2 and census.text_pages == 0
    assert census.image_only is True


def test_empty_document_is_not_image_only(tmp_path: Path):
    import pymupdf

    path = tmp_path / "empty.pdf"
    doc = pymupdf.open()
    doc.save(str(path))
    assert pdf_text_layer_census(path).image_only is False
```

- [x] **Step 2: Run to verify failure** — `./scripts/test.sh tests/parser/pdf2md/test_census.py -v` → `ModuleNotFoundError`.

- [x] **Step 3: Implement**

```python
"""Does this PDF carry a text layer? (PyMuPDF word census per page.)"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Census:
    pages: int
    text_pages: int

    @property
    def image_only(self) -> bool:
        return self.pages > 0 and self.text_pages == 0


def pdf_text_layer_census(path: Path) -> Census:
    import pymupdf  # AGPL; only under the [pdf2md] extra

    doc = pymupdf.open(str(path))
    try:
        text_pages = sum(1 for page in doc if page.get_text("words"))
        return Census(pages=doc.page_count, text_pages=text_pages)
    finally:
        doc.close()
```

- [x] **Step 4: Run to verify pass**, then **Step 5: Commit** — `git commit -m "feat(pdf2md): text-layer census"`.

---

### Task 3: `.textpack` packing, front matter, manifest

**Files:**
- Create: `ontorag/parser/pdf2md/textpack.py`
- Test: `tests/parser/pdf2md/test_textpack.py`

**Interfaces:**
- Produces: `parse_front_matter(markdown: str) -> tuple[dict[str, Any], str]` (metadata, body-without-front-matter).
- Produces: `build_manifest(*, source: Path, source_format: str, front_matter: dict, stats: dict, ocr: dict | None, warnings: list[str]) -> dict[str, Any]`.
- Produces: `pack_textpack(*, out_path: Path, stem: str, body: str, figure_dir: Path | None, manifest: dict) -> Path` (zip with `<stem>.md`, `figs/<sha12>.<ext>`, `pdf2md.json`; links rewritten).
- Produces: `MANIFEST_NAME = "pdf2md.json"`, `BIBLIOGRAPHIC_KEYS`.

- [x] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from ontorag.parser.markdown.parser import NativeMarkdownParser
from ontorag.parser.pdf2md.textpack import (
    MANIFEST_NAME,
    build_manifest,
    pack_textpack,
    parse_front_matter,
)

pytestmark = pytest.mark.offline

_MD = """---
title: The Cognitive Structure of Emotions
edition: Second Edition
authors:
  - Andrew Ortony
  - Gerald Clore
publisher: Cambridge University Press
year: 2022
isbn:
  - 9781108844246
  - 9781108934053
pages: 575
source: book.pdf
generator: pdf2md
---

# The Cognitive Structure of Emotions

![Figure 1](figs/fig-p012-340.png)

Body ![dup](figs/fig-p099-001.png) end.
"""


def test_parse_front_matter_scalars_and_lists():
    meta, body = parse_front_matter(_MD)
    assert meta["title"] == "The Cognitive Structure of Emotions"
    assert meta["authors"] == ["Andrew Ortony", "Gerald Clore"]
    assert meta["isbn"] == ["9781108844246", "9781108934053"]
    assert meta["year"] == 2022 and meta["pages"] == 575
    assert body.startswith("# The Cognitive Structure of Emotions")
    assert "---" not in body.split("\n", 1)[0]


def test_parse_front_matter_absent_returns_empty_meta():
    meta, body = parse_front_matter("# Just a heading\n")
    assert meta == {} and body == "# Just a heading\n"


def test_parse_front_matter_quoted_scalar():
    meta, _ = parse_front_matter('---\ntitle: "A: colon"\n---\nx\n')
    assert meta["title"] == "A: colon"


def test_build_manifest_keeps_only_found_bibliographic_keys(tmp_path: Path):
    src = tmp_path / "book.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    meta, _ = parse_front_matter(_MD)
    manifest = build_manifest(
        source=src, source_format="pdf", front_matter=meta,
        stats={"doc_type": "book", "doc_scores": {"book": 14.0}, "pages": 575,
               "figure_regions": 2, "tables": 5},
        ocr=None, warnings=[],
    )
    assert manifest["schema"] == 1
    assert manifest["source"]["name"] == "book.pdf"
    assert manifest["source"]["sha256"] == hashlib.sha256(b"%PDF-1.4 fake").hexdigest()
    assert manifest["bibliographic"] == {
        "title": "The Cognitive Structure of Emotions", "edition": "Second Edition",
        "authors": ["Andrew Ortony", "Gerald Clore"], "publisher": "Cambridge University Press",
        "year": 2022, "isbn": ["9781108844246", "9781108934053"],
    }
    assert "arxiv" not in manifest["bibliographic"]
    assert manifest["doc_type"] == "book" and manifest["pages"] == 575
    assert manifest["ocr"] is None


def test_pack_textpack_dedupes_figures_and_rewrites_links(tmp_path: Path):
    figs = tmp_path / "figs"
    figs.mkdir()
    png = b"\x89PNG\r\n\x1a\nsame-bytes"
    (figs / "fig-p012-340.png").write_bytes(png)
    (figs / "fig-p099-001.png").write_bytes(png)  # identical content, different name
    _, body = parse_front_matter(_MD)
    out = pack_textpack(
        out_path=tmp_path / "book.textpack", stem="book", body=body,
        figure_dir=figs, manifest={"schema": 1},
    )
    with zipfile.ZipFile(out) as z:
        names = sorted(z.namelist())
        sha12 = hashlib.sha256(png).hexdigest()[:12]
        assert names == [MANIFEST_NAME, "book.md", f"figs/{sha12}.png"]
        md = z.read("book.md").decode("utf-8")
        assert md.count(f"](figs/{sha12}.png)") == 2
        assert "fig-p012" not in md
        assert json.loads(z.read(MANIFEST_NAME))["schema"] == 1


def test_pack_textpack_round_trips_through_markdown_parser(tmp_path: Path):
    out = pack_textpack(
        out_path=tmp_path / "book.textpack", stem="book", body="# T\n\nbody\n",
        figure_dir=None, manifest={"schema": 1},
    )
    parser = NativeMarkdownParser()
    md_text, bundle_root = parser._open_textpack(out, tmp_path / "unpack")
    assert md_text == "# T\n\nbody\n"
    assert (bundle_root / MANIFEST_NAME).is_file()
```

- [x] **Step 2: Run to verify failure** — `./scripts/test.sh tests/parser/pdf2md/test_textpack.py -v` → `ModuleNotFoundError`.

- [x] **Step 3: Implement**

```python
"""Front matter, manifest and .textpack packing for the pdf2md engine.

The bundle shape is what ``NativeMarkdownParser._open_textpack`` accepts: one
``*.md`` at the zip root plus assets referenced by relative links. The manifest
is an extra file the Markdown parser ignores and the catalog reads.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_NAME = "pdf2md.json"
BIBLIOGRAPHIC_KEYS = ("title", "edition", "authors", "publisher", "year", "isbn", "arxiv", "language")

_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_LINK_RE = re.compile(r"(!\[[^\]]*\]\()figs/([^)]+)(\))")


def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        return inner.replace('\\"', '"') if raw[0] == '"' else inner
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_front_matter(markdown: str) -> tuple[dict[str, Any], str]:
    """Parse pdf2md's YAML front matter (scalars and ``- item`` lists only)."""
    m = _FRONT_MATTER_RE.match(markdown)
    if not m:
        return {}, markdown
    meta: dict[str, Any] = {}
    current: str | None = None
    for line in m.group(1).splitlines():
        if line.startswith("  - ") and current is not None:
            meta.setdefault(current, [])
            if not isinstance(meta[current], list):
                meta[current] = [meta[current]]
            meta[current].append(_scalar(line[4:]))
            continue
        key, sep, value = line.partition(":")
        if not sep or line.startswith(" "):
            continue
        key = key.strip()
        current = key
        meta[key] = _scalar(value) if value.strip() else []
    return meta, markdown[m.end():].lstrip("\n")


def build_manifest(*, source: Path, source_format: str, front_matter: dict[str, Any],
                   stats: dict[str, Any], ocr: dict[str, Any] | None,
                   warnings: list[str]) -> dict[str, Any]:
    from ontorag._version import __version__

    data = source.read_bytes()
    bibliographic = {k: front_matter[k] for k in BIBLIOGRAPHIC_KEYS
                     if k in front_matter and front_matter[k] not in ("", [], None)}
    return {
        "schema": 1,
        "source": {"name": source.name, "format": source_format,
                   "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
        "bibliographic": bibliographic,
        "doc_type": stats.get("doc_type") or front_matter.get("type"),
        "doc_scores": stats.get("doc_scores", {}),
        "pages": stats.get("pages", front_matter.get("pages")),
        "figures": stats.get("figure_regions", 0),
        "tables": stats.get("tables", 0),
        "ocr": ocr,
        "converter": {"pdf2md": "pdf2md-files.zip 2026-09-02", "ontorag": __version__},
        "warnings": list(warnings),
    }


def pack_textpack(*, out_path: Path, stem: str, body: str, figure_dir: Path | None,
                  manifest: dict[str, Any]) -> Path:
    renames: dict[str, str] = {}
    blobs: dict[str, bytes] = {}
    if figure_dir is not None and figure_dir.is_dir():
        for fig in sorted(figure_dir.iterdir()):
            if not fig.is_file():
                continue
            raw = fig.read_bytes()
            new_name = f"{hashlib.sha256(raw).hexdigest()[:12]}{fig.suffix.lower()}"
            renames[fig.name] = new_name
            blobs.setdefault(new_name, raw)

    def _rewrite(m: re.Match[str]) -> str:
        return f"{m.group(1)}figs/{renames.get(m.group(2), m.group(2))}{m.group(3)}"

    body = _LINK_RE.sub(_rewrite, body)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{stem}.md", body)
        for name, raw in sorted(blobs.items()):
            z.writestr(f"figs/{name}", raw)
        z.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=1))
    tmp.replace(out_path)
    return out_path
```

- [x] **Step 4: Run to verify pass** (6 PASS), **Step 5: Commit** — `git commit -m "feat(pdf2md): textpack packing, front matter and manifest"`.

---

### Task 4: OCR in place

**Files:**
- Create: `ontorag/parser/pdf2md/ocr.py`
- Test: `tests/parser/pdf2md/test_ocr.py`

**Interfaces:**
- Produces: `ocr_in_place(source: Path, *, originals_dirname: str = "__originals__", engine: str = "tesseract", languages: str = "eng", deskew: bool = True, timeout: int = 1800, runner: Callable[[Path, Path, OcrSettings], None] | None = None) -> OcrResult` where `OcrResult(applied: bool, backup: Path, engine: str, languages: str)`.
- Produces: `OcrError(RuntimeError)`, `OcrProducedNoTextError(OcrError)`.
- Consumes: `pdf_text_layer_census` (Task 2).

- [x] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

from ontorag.parser.pdf2md import ocr as ocr_mod  # noqa: E402
from ontorag.parser.pdf2md.ocr import OcrError, OcrProducedNoTextError, ocr_in_place  # noqa: E402
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
    settings = ocr_mod.OcrSettings(engine="appleocr", languages="eng,deu", deskew=True, timeout=42)
    ocr_mod._run_ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", settings)
    cmd = calls["cmd"]
    assert cmd[1:3] == ["-m", "ocrmypdf"]
    assert "--skip-text" in cmd and "--deskew" in cmd
    assert cmd[cmd.index("-l") + 1] == "eng+deu"
    assert cmd[cmd.index("--ocr-engine") + 1] == "appleocr"
    assert calls["timeout"] == 42


def test_default_runner_omits_ocr_engine_flag_for_tesseract(monkeypatch, tmp_path: Path):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr(ocr_mod.subprocess, "run", fake_run)
    ocr_mod._run_ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", ocr_mod.OcrSettings())
    assert "--ocr-engine" not in calls["cmd"]
```

- [x] **Step 2: Run to verify failure** — `./scripts/test.sh tests/parser/pdf2md/test_ocr.py -v` → `ModuleNotFoundError`.

- [x] **Step 3: Implement**

```python
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
    cmd = [sys.executable, "-m", "ocrmypdf", "--skip-text",
           "-l", "+".join(p.strip() for p in settings.languages.split(",") if p.strip())]
    if settings.deskew:
        cmd.append("--deskew")
    if settings.engine and settings.engine != "tesseract":
        cmd += ["--ocr-engine", settings.engine]
    cmd += [str(source), str(output)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.timeout, check=False)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise OcrError(f"ocrmypdf exited {proc.returncode}: {' | '.join(tail) or 'no stderr'}")


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
    settings = OcrSettings(engine=engine, languages=languages, deskew=deskew, timeout=timeout)
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
        raise OcrError(f"{source.name}: OCR timed out after {settings.timeout}s") from exc
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise OcrError(f"{source.name}: {exc}") from exc
    return OcrResult(applied=True, backup=backup, engine=settings.engine, languages=settings.languages)
```

Note: `OcrProducedNoTextError` is raised inside the `try` and is an `OcrError`, so the first `except OcrError` branch removes the temp and re-raises it unchanged.

- [x] **Step 4: Run to verify pass** (6 PASS), **Step 5: Commit** — `git commit -m "feat(pdf2md): OCR in place with original backup"`.

---

### Task 5: Probes, engine registration, packaging

**Files:**
- Create: `ontorag/parser/pdf2md/probe.py`
- Modify: `ontorag/constants.py:369-372` (add `PARSER_ENGINE_PDF2MD`)
- Modify: `ontorag/parser/registry.py:~262` (`_REGISTRY` dict) and imports
- Modify: `pyproject.toml:55` (`[project.optional-dependencies]`)
- Modify: `tests/parser/test_registry.py:15` (engine set assertion)
- Test: `tests/parser/pdf2md/test_probe.py`, `tests/parser/pdf2md/test_registration.py`

**Interfaces:**
- Produces: `check_pdf2md_available() -> bool`; `check_ocr_available() -> str | None`; `check_soffice_available(explicit: str | None = None) -> str | None` (all stdlib only; `None` = OK).
- Produces: registry key `"pdf2md"`; `PARSER_ENGINE_PDF2MD`.

- [x] **Step 1: Write the failing tests**

`tests/parser/pdf2md/test_probe.py`:

```python
import importlib.util

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
```

`tests/parser/pdf2md/test_registration.py`:

```python
import pytest

from ontorag.constants import PARSER_ENGINE_PDF2MD
from ontorag.parser import registry

pytestmark = pytest.mark.offline


def test_pdf2md_spec_registered():
    spec = registry.parser_specs_snapshot()[PARSER_ENGINE_PDF2MD]
    assert spec.impl == "ontorag.parser.pdf2md.parser:Pdf2MdParser"
    assert spec.suffixes == frozenset({"pdf", "epub", "docx", "doc", "odt", "rtf"})
    assert spec.queue_group == "pdf2md"
    assert spec.concurrency == 2
    assert spec.endpoint_requirement() == "pip install 'ontorag[pdf2md]'"


def test_pdf2md_unavailable_falls_through_routing(monkeypatch):
    from ontorag.parser.pdf2md import probe
    from ontorag.parser.routing import resolve_file_parser_engine

    monkeypatch.setattr(probe, "check_pdf2md_available", lambda: False)
    monkeypatch.setenv("ONTORAG_PARSER", "pdf:pdf2md,*:legacy")
    assert resolve_file_parser_engine("book.pdf") == "legacy"


def test_pdf2md_available_claims_pdf(monkeypatch):
    from ontorag.parser.pdf2md import probe
    from ontorag.parser.routing import resolve_file_parser_engine

    monkeypatch.setattr(probe, "check_pdf2md_available", lambda: True)
    monkeypatch.setenv("ONTORAG_PARSER", "pdf:pdf2md,*:legacy")
    assert resolve_file_parser_engine("book.pdf") == "pdf2md"
```

- [x] **Step 2: Run to verify failure** — both files → `ImportError` / `KeyError`.

- [x] **Step 3: Implement `probe.py`**

```python
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
        return None if Path(explicit).is_file() else f"PDF2MD_SOFFICE points at a missing file: {explicit}"
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return None
    return "DOC/ODT/RTF conversion needs LibreOffice (soffice) on PATH or PDF2MD_SOFFICE"
```

- [x] **Step 4: Register the engine**

`ontorag/constants.py` after line 372:

```python
PARSER_ENGINE_PDF2MD = "pdf2md"
```

`ontorag/parser/registry.py`: import `PARSER_ENGINE_PDF2MD` alongside the other engine constants, add near the top

```python
def _pdf2md_available() -> bool:
    from ontorag.parser.pdf2md import probe  # stdlib-only module

    return probe.check_pdf2md_available()


def _pdf2md_requirement() -> str | None:
    from ontorag.parser.pdf2md import probe

    return None if probe.check_pdf2md_available() else probe.INSTALL_HINT
```

and add to `_REGISTRY` after the docling entry:

```python
    PARSER_ENGINE_PDF2MD: ParserSpec(
        engine_name=PARSER_ENGINE_PDF2MD,
        impl="ontorag.parser.pdf2md.parser:Pdf2MdParser",
        suffixes=frozenset({"pdf", "epub", "docx", "doc", "odt", "rtf"}),
        queue_group=PARSER_ENGINE_PDF2MD,
        concurrency=int(os.getenv("MAX_PARALLEL_PARSE_PDF2MD", "2")),
        # Local engine, but only usable when the [pdf2md] extra is installed;
        # routing treats "unavailable" exactly like an unconfigured endpoint.
        endpoint_configured=_pdf2md_available,
        endpoint_requirement=lambda: "pip install 'ontorag[pdf2md]'",
    ),
```

(`import os` if not already imported.) Note `test_pdf2md_spec_registered` asserts the literal `endpoint_requirement()` string; keep it identical to `probe.INSTALL_HINT`.

`pyproject.toml`, inside `[project.optional-dependencies]` after the `api` group:

```toml
# Markdown-canonical intake via the vendored pdf2md converter + OCRmyPDF.
# PyMuPDF is AGPL-3.0: install this extra only where that licence is acceptable.
# System packages also needed for OCR: tesseract-ocr, ghostscript; for
# DOC/ODT/RTF input: libreoffice (soffice on PATH).
pdf2md = [
    "pymupdf>=1.24",
    "ocrmypdf>=16",
]
```

`tests/parser/test_registry.py:15` — update the expected set to include `"pdf2md"`:

```python
    assert engines == frozenset({"native", "legacy", "mineru", "docling", "pdf2md"})
```

- [x] **Step 5: Run to verify pass** — `./scripts/test.sh tests/parser/pdf2md tests/parser/test_registry.py tests/parser/test_plugins.py -v`. If `test_pdf2md_unavailable_falls_through_routing` fails because routing does not consult `endpoint_configured` for local engines, read `ontorag/parser/routing.py` for where `engine_endpoint_configured` is called and confirm it is applied to every spec (it is for mineru/docling); adjust the monkeypatch target to `registry._pdf2md_available` if routing bound the callable at import.

- [x] **Step 6: Commit** — `uv sync --extra pdf2md` (updates `uv.lock`), then `git add -A && git commit -m "feat(pdf2md): register engine, availability probes, [pdf2md] extra"`.

---

### Task 6: `ParseResult.canonical_source` / `document_metadata` in the parse stage

**Files:**
- Modify: `ontorag/parser/base.py:114-152` (`ParseResult`)
- Modify: `ontorag/pipeline.py:4429-4432` (after `parse_engine` stamping, before the content_hash refresh)
- Modify: `ontorag/utils_pipeline.py:374-389` (`_DOC_STATUS_METADATA_CARRY_OVER_KEYS`)
- Test: `tests/parser/test_parse_result_canonical_source.py`, `tests/pipeline/test_canonical_source.py`, `tests/pipeline/_fake_conv_parser.py`

**Interfaces:**
- Produces: `ParseResult.canonical_source: str | None = None`, `ParseResult.document_metadata: dict[str, Any] | None = None`; both emitted by `to_dict()` only when set.
- Produces (pipeline behaviour): when `parsed_data["canonical_source"]` is set, `doc_status.file_path` = its canonical basename, `metadata.source_file` = its basename, `metadata.source_file_original` = the previous `file_path`; `document_metadata` merged into `metadata`; `full_docs` row's `file_path`/`source_file` updated.

- [x] **Step 1: Write the failing unit test**

`tests/parser/test_parse_result_canonical_source.py`:

```python
import pytest

from ontorag.parser.base import ParseResult

pytestmark = pytest.mark.offline


def test_to_dict_omits_new_fields_when_unset():
    r = ParseResult(doc_id="d", file_path="a.pdf", parse_format="raw", content="x")
    assert "canonical_source" not in r.to_dict()
    assert "document_metadata" not in r.to_dict()


def test_to_dict_emits_new_fields_when_set():
    r = ParseResult(doc_id="d", file_path="a.pdf", parse_format="raw", content="x",
                    canonical_source="a.textpack", document_metadata={"doc_type": "book"})
    d = r.to_dict()
    assert d["canonical_source"] == "a.textpack"
    assert d["document_metadata"] == {"doc_type": "book"}
```

- [x] **Step 2: Run to verify failure** — `TypeError: unexpected keyword argument 'canonical_source'`.

- [x] **Step 3: Implement the dataclass fields**

In `ParseResult` after `smartheading_llm_cache_ids`:

```python
    # Set by converter engines (pdf2md): the generated bundle that becomes
    # the document of record. The parse stage re-points doc_status.file_path
    # / metadata.source_file to it and records the enqueued name as
    # metadata.source_file_original. Never set by ordinary engines.
    canonical_source: str | None = None
    # Catalog metadata merged into doc_status.metadata at the PARSING upsert
    # (bibliographic, doc_type, doc_scores, ocr, converter).
    document_metadata: dict[str, Any] | None = None
```

and in `to_dict()` before `return out`:

```python
        if self.canonical_source:
            out["canonical_source"] = self.canonical_source
        if self.document_metadata:
            out["document_metadata"] = self.document_metadata
```

- [x] **Step 4: Run unit test → PASS. Write the failing pipeline test**

`tests/pipeline/_fake_conv_parser.py` (a registrable engine that behaves like a converter):

```python
"""Test-only converter engine: parses like legacy but re-points the source."""

from __future__ import annotations

from ontorag.constants import FULL_DOCS_FORMAT_RAW
from ontorag.parser.base import BaseParser, ParseContext, ParseResult


class FakeConvParser(BaseParser):
    engine_name = "fakeconv"

    async def parse(self, ctx: ParseContext) -> ParseResult:
        source = ctx.source_path(self.engine_name)
        body = source.read_text(encoding="utf-8")
        bundle = source.with_suffix(".textpack")
        bundle.write_text(body, encoding="utf-8")  # stands in for the real bundle
        await ctx.rag._persist_parsed_full_docs(
            ctx.doc_id, {"content": body, "parse_format": FULL_DOCS_FORMAT_RAW}
        )
        return ParseResult(
            doc_id=ctx.doc_id,
            file_path=ctx.file_path,
            parse_format=FULL_DOCS_FORMAT_RAW,
            content=body,
            parse_engine=self.engine_name,
            canonical_source=bundle.name,
            document_metadata={"doc_type": "document", "bibliographic": {"title": "T"}},
        )
```

`tests/pipeline/test_canonical_source.py` — model construction on `tests/pipeline/test_pipeline_release_closure.py::_new_rag` (copy `_SimpleTokenizerImpl`, `_mock_embedding`, `_mock_llm` and the `OntoRAG(...)` call from lines 51-102 of that file):

```python
"""A converter engine's ParseResult.canonical_source re-points the document."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from ontorag import OntoRAG
from ontorag.base import DocStatus
from ontorag.constants import FULL_DOCS_FORMAT_PENDING_PARSE
from ontorag.parser.registry import ParserSpec, register_parser
from ontorag.utils import EmbeddingFunc, Tokenizer

pytestmark = pytest.mark.offline


class _CharTokenizer:
    def encode(self, s):  # noqa: D401
        return [ord(c) for c in s]

    def decode(self, t):
        return "".join(chr(x) for x in t)


async def _emb(texts):
    return np.random.rand(len(texts), 32)


async def _llm(prompt, **kw):
    return "(entity<|#|>T<|#|>CONCEPT<|#|>desc)<|COMPLETE|>"


def _rag(tmp_path: Path) -> OntoRAG:
    return OntoRAG(
        working_dir=str(tmp_path / "wd"),
        workspace=f"cs-{tmp_path.name}",
        llm_model_func=_llm,
        embedding_func=EmbeddingFunc(embedding_dim=32, max_token_size=4096, func=_emb),
        tokenizer=Tokenizer("char", _CharTokenizer()),
    )


def test_canonical_source_repoints_doc_status_and_archives_bundle(tmp_path, monkeypatch):
    register_parser(ParserSpec(
        engine_name="fakeconv",
        impl="tests.pipeline._fake_conv_parser:FakeConvParser",
        suffixes=frozenset({"pdf"}),
        queue_group="fakeconv",
        concurrency=1,
    ))
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    monkeypatch.setenv("INPUT_DIR", str(inputs))
    src = inputs / "book.pdf"
    src.write_text("# Title\n\nbody text\n", encoding="utf-8")

    async def _run():
        rag = _rag(tmp_path)
        await rag.initialize_storages()
        try:
            track = await rag.apipeline_enqueue_documents(
                "", file_paths=str(src), docs_format=FULL_DOCS_FORMAT_PENDING_PARSE,
                parse_engine="fakeconv", process_options="F",
            )
            await rag.apipeline_process_enqueue_documents()
            docs = await rag.doc_status.get_docs_by_track_id(track)
            assert len(docs) == 1
            doc = next(iter(docs.values()))
            status = doc["status"] if isinstance(doc, dict) else doc.status
            file_path = doc["file_path"] if isinstance(doc, dict) else doc.file_path
            meta = doc["metadata"] if isinstance(doc, dict) else doc.metadata
            assert status == DocStatus.PROCESSED
            assert file_path == "book.textpack"
            assert meta["source_file"] == "book.textpack"
            assert meta["source_file_original"] == "book.pdf"
            assert meta["doc_type"] == "document"
            assert meta["bibliographic"] == {"title": "T"}
            # residency: original untouched, bundle archived
            assert src.exists()
            assert (inputs / "__parsed__" / "book.textpack").exists()
        finally:
            await rag.finalize_storages()

    asyncio.run(_run())
```

If `get_docs_by_track_id` is not the accessor's name, use `await rag.doc_status.get_docs_by_statuses([DocStatus.PROCESSED])` and pick the single row.

- [x] **Step 5: Run to verify failure** — `assert file_path == "book.textpack"` fails with `'book.pdf'`.

- [x] **Step 6: Implement the parse-stage application**

`ontorag/utils_pipeline.py` — extend the tuple:

```python
    "analyzing_start_time",
    # Converter engines (pdf2md): the bundle became the document of record.
    "source_file_original",
    "bibliographic",
    "doc_type",
    "doc_scores",
    "ocr",
    "converter",
```

`ontorag/pipeline.py` — immediately after the two lines that set `status_doc_w.metadata["parse_format"]` / `["parse_engine"]` (line ~4431), insert:

```python
                # Converter engines hand back a generated bundle that becomes
                # the document of record: re-point file_path / source_file to
                # it and remember the enqueued name. The bundle is what the
                # source resolver, archive step and citations now see; the
                # original file is never referenced again and stays put.
                canonical_source_w = parsed_data_w.get("canonical_source")
                if isinstance(canonical_source_w, str) and canonical_source_w.strip():
                    original_file_path_w = file_path_w
                    file_path_w = normalize_document_file_path(canonical_source_w)
                    status_doc_w.file_path = file_path_w
                    status_doc_w.metadata["source_file"] = Path(canonical_source_w).name
                    status_doc_w.metadata["source_file_original"] = original_file_path_w
                    await self._persist_parsed_full_docs(
                        doc_id_w,
                        {
                            "file_path": file_path_w,
                            "source_file": Path(canonical_source_w).name,
                        },
                    )
                document_metadata_w = parsed_data_w.get("document_metadata")
                if isinstance(document_metadata_w, dict):
                    status_doc_w.metadata.update(document_metadata_w)
```

`normalize_document_file_path` and `Path` are already imported in `pipeline.py` (verify with `grep -n "normalize_document_file_path\|^from pathlib" ontorag/pipeline.py`). The later `_upsert_doc_status_transition(..., file_path=file_path_w)` then persists the new name because the upsert payload writes `"file_path": file_path`.

Archive: `FakeConvParser` does not archive; the real engine delegates to the Markdown parser which calls `ctx.archive_source(bundle)`. To make the test's archive assertion hold for the fake, add at the end of `FakeConvParser.parse` before `return`: `await ctx.archive_source(str(bundle))`.

- [x] **Step 7: Run to verify pass** — both new test files and `./scripts/test.sh tests/pipeline -q` (no regressions).

- [x] **Step 8: Commit** — `git commit -m "feat(parser): ParseResult.canonical_source re-points the document of record"`.

---

### Task 7: `Pdf2MdParser` — orchestration and delegation

**Files:**
- Create: `ontorag/parser/pdf2md/parser.py`
- Test: `tests/parser/pdf2md/test_parser.py`

**Interfaces:**
- Consumes: Tasks 1–5 functions; `NativeMarkdownParser` (`ontorag.parser.markdown.parser`); `ParseContext.resolve`, `ctx.archive_source` (via the delegate).
- Produces: `Pdf2MdParser(BaseParser)` with `engine_name = "pdf2md"` and `async parse(ctx) -> ParseResult` whose result has `parse_engine="pdf2md"`, `canonical_source="<stem>.textpack"`, `document_metadata` from the manifest.
- Produces: `Pdf2MdSettings.from_env()` (reads the §5.4 env vars).

- [x] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")

import ontorag.pipeline as _pipeline  # noqa: E402
from ontorag.constants import FULL_DOCS_FORMAT_ONTORAG  # noqa: E402
from ontorag.parser.base import ParseContext  # noqa: E402
from ontorag.parser.pdf2md import parser as p2m  # noqa: E402
from ontorag.parser.pdf2md.convert import ConversionResult, Pdf2MdConversionError  # noqa: E402
from ontorag.parser.pdf2md.parser import Pdf2MdParser  # noqa: E402
from tests.parser.pdf2md.conftest import make_text_pdf  # noqa: E402

pytestmark = pytest.mark.offline

_MD = "---\ntitle: T\nauthors:\n  - A\n---\n\n# T\n\nBody text.\n"


class _FakeRag:
    def __init__(self):
        self.persisted = []

    async def _persist_parsed_full_docs(self, doc_id, payload):
        self.persisted.append((doc_id, payload))

    def _resolve_source_file_for_parser(self, file_path, *, source_file=None, parser_engine=None):
        return file_path


@pytest.fixture
def archived(monkeypatch):
    calls = []

    async def _record(source_path):
        calls.append(Path(source_path))
        return source_path

    monkeypatch.setattr(_pipeline, "archive_docx_source_after_full_docs_sync", _record)
    return calls


def _fake_convert(markdown=_MD, stats=None):
    def _convert(source, work_dir, **kw):
        work_dir.mkdir(parents=True, exist_ok=True)
        return ConversionResult(markdown=markdown, figure_dir=None,
                                stats=stats or {"doc_type": "book", "pages": 2}, stdout="")

    return _convert


async def test_parse_text_pdf_delegates_and_repoints(text_pdf: Path, archived, monkeypatch):
    monkeypatch.setattr(p2m, "convert_source", _fake_convert())
    rag = _FakeRag()
    result = await Pdf2MdParser().parse(ParseContext(rag, "doc-1", str(text_pdf), {"parse_engine": "pdf2md"}))

    bundle = text_pdf.with_suffix(".textpack")
    assert bundle.is_file()
    assert result.parse_engine == "pdf2md"
    assert result.parse_format == FULL_DOCS_FORMAT_ONTORAG
    assert result.canonical_source == "book.textpack"
    assert result.document_metadata["doc_type"] == "book"
    assert result.document_metadata["bibliographic"] == {"title": "T", "authors": ["A"]}
    assert result.document_metadata["ocr"] is None
    assert result.blocks_path.endswith(".blocks.jsonl")
    # the Markdown parser archived the bundle, not the PDF
    assert archived == [bundle]
    assert text_pdf.is_file()
    with zipfile.ZipFile(bundle) as z:
        assert "pdf2md.json" in z.namelist()
        assert z.read("book.md").decode().startswith("# T")


async def test_parse_image_only_pdf_runs_ocr_first(image_pdf: Path, archived, monkeypatch):
    seen = {}

    def fake_ocr(source, **kw):
        seen["source"] = source
        make_text_pdf(source)  # simulate in-place replacement
        return p2m.OcrResult(applied=True, backup=source.parent / "__originals__" / source.name,
                             engine="tesseract", languages="eng")

    monkeypatch.setattr(p2m, "ocr_in_place", fake_ocr)
    monkeypatch.setattr(p2m, "convert_source", _fake_convert())
    monkeypatch.setattr(p2m, "check_ocr_available", lambda: None)
    result = await Pdf2MdParser().parse(ParseContext(_FakeRag(), "doc-2", str(image_pdf), {"parse_engine": "pdf2md"}))
    assert seen["source"] == image_pdf
    assert result.document_metadata["ocr"]["applied"] is True
    assert result.document_metadata["ocr"]["original_backup"] == "__originals__/scan.pdf"


async def test_parse_image_only_pdf_without_ocr_prereqs_fails_clearly(image_pdf: Path, monkeypatch):
    monkeypatch.setattr(p2m, "check_ocr_available", lambda: "OCR for scanned PDFs is unavailable; missing: tesseract")
    with pytest.raises(ValueError, match="missing: tesseract"):
        await Pdf2MdParser().parse(ParseContext(_FakeRag(), "doc-3", str(image_pdf), {"parse_engine": "pdf2md"}))


async def test_parse_reuses_existing_bundle_for_unchanged_source(text_pdf: Path, archived, monkeypatch):
    calls = {"n": 0}

    def counting(source, work_dir, **kw):
        calls["n"] += 1
        return _fake_convert()(source, work_dir, **kw)

    monkeypatch.setattr(p2m, "convert_source", counting)
    ctx = ParseContext(_FakeRag(), "doc-4", str(text_pdf), {"parse_engine": "pdf2md"})
    await Pdf2MdParser().parse(ctx)
    # The delegate archived (moved) the bundle in real life; the fake archive
    # leaves it in place, so a second parse must find and reuse it.
    await Pdf2MdParser().parse(ctx)
    assert calls["n"] == 1


async def test_conversion_error_is_surfaced(text_pdf: Path, monkeypatch):
    def boom(source, work_dir, **kw):
        raise Pdf2MdConversionError("no text layer")

    monkeypatch.setattr(p2m, "convert_source", boom)
    with pytest.raises(ValueError, match="no text layer"):
        await Pdf2MdParser().parse(ParseContext(_FakeRag(), "doc-5", str(text_pdf), {"parse_engine": "pdf2md"}))


async def test_unsupported_suffix_rejected(tmp_path: Path):
    src = tmp_path / "notes.txt"
    src.write_text("x")
    with pytest.raises(ValueError, match="does not support"):
        await Pdf2MdParser().parse(ParseContext(_FakeRag(), "doc-6", str(src), {"parse_engine": "pdf2md"}))
```

- [x] **Step 2: Run to verify failure** — `ModuleNotFoundError: ontorag.parser.pdf2md.parser`.

- [x] **Step 3: Implement**

```python
"""pdf2md engine: convert to a Markdown-canonical .textpack, then delegate.

Order of work for one source (all blocking work runs off the event loop):
reuse check -> text-layer census (PDF) -> OCR in place (image-only PDF) ->
pdf2md conversion -> pack .textpack -> NativeMarkdownParser.parse on the
bundle. The Markdown parser writes the sidecar, persists full_docs and
archives the bundle; this engine only adds ``canonical_source`` and the
catalog metadata to the result.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ontorag.constants import PARSER_ENGINE_NATIVE, PARSER_ENGINE_PDF2MD, PARSED_DIR_NAME
from ontorag.parser.base import BaseParser, ParseContext, ParseResult
from ontorag.parser.pdf2md.census import pdf_text_layer_census
from ontorag.parser.pdf2md.convert import Pdf2MdConversionError, convert_source
from ontorag.parser.pdf2md.ocr import OcrError, OcrResult, ocr_in_place
from ontorag.parser.pdf2md.probe import check_ocr_available, check_soffice_available
from ontorag.parser.pdf2md.textpack import (
    MANIFEST_NAME,
    build_manifest,
    pack_textpack,
    parse_front_matter,
)
from ontorag.utils import logger

SUPPORTED_SUFFIXES = frozenset({".pdf", ".epub", ".docx", ".doc", ".odt", ".rtf"})
_SOFFICE_SUFFIXES = frozenset({".doc", ".odt", ".rtf"})


@dataclass(frozen=True)
class Pdf2MdSettings:
    ocr_engine: str = "tesseract"
    ocr_languages: str = "eng"
    ocr_deskew: bool = True
    ocr_timeout: int = 1800
    originals_dirname: str = "__originals__"
    soffice: str | None = None
    figure_dpi: int = 200

    @classmethod
    def from_env(cls) -> "Pdf2MdSettings":
        def _bool(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")

        return cls(
            ocr_engine=os.getenv("PDF_OCR_ENGINE", "tesseract").strip() or "tesseract",
            ocr_languages=os.getenv("PDF_OCR_LANGUAGES", "eng").strip() or "eng",
            ocr_deskew=_bool("PDF_OCR_DESKEW", True),
            ocr_timeout=int(os.getenv("PDF_OCR_TIMEOUT", "1800") or 1800),
            originals_dirname=os.getenv("PDF2MD_ORIGINALS_DIRNAME", "__originals__").strip() or "__originals__",
            soffice=(os.getenv("PDF2MD_SOFFICE") or "").strip() or None,
            figure_dpi=int(os.getenv("PDF2MD_FIGURE_DPI", "200") or 200),
        )


def _bundle_matches_source(bundle: Path, source: Path) -> bool:
    """True when ``bundle`` carries a manifest whose source sha256 equals ``source``'s."""
    import hashlib

    try:
        with zipfile.ZipFile(bundle) as z:
            manifest = json.loads(z.read(MANIFEST_NAME))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return False
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return manifest.get("source", {}).get("sha256") == digest


class Pdf2MdParser(BaseParser):
    engine_name = PARSER_ENGINE_PDF2MD

    def _convert_to_bundle(self, source: Path, bundle: Path, settings: Pdf2MdSettings) -> dict[str, Any]:
        """Blocking: census -> OCR -> pdf2md -> pack. Returns the manifest."""
        suffix = source.suffix.lower()
        warnings: list[str] = []
        ocr_info: dict[str, Any] | None = None

        if suffix == ".pdf" and pdf_text_layer_census(source).image_only:
            problem = check_ocr_available()
            if problem:
                raise ValueError(f"{source.name}: image-only PDF and {problem}")
            result: OcrResult = ocr_in_place(
                source,
                originals_dirname=settings.originals_dirname,
                engine=settings.ocr_engine,
                languages=settings.ocr_languages,
                deskew=settings.ocr_deskew,
                timeout=settings.ocr_timeout,
            )
            ocr_info = {
                "applied": result.applied,
                "engine": result.engine,
                "languages": result.languages,
                "original_backup": f"{settings.originals_dirname}/{result.backup.name}",
            }
        if suffix in _SOFFICE_SUFFIXES:
            problem = check_soffice_available(settings.soffice)
            if problem:
                raise ValueError(f"{source.name}: {problem}")

        work_root = bundle.parent / PARSED_DIR_NAME
        work_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=f"{source.stem}.pdf2md-", dir=work_root))
        try:
            conv = convert_source(source, work_dir, soffice=settings.soffice, figure_dpi=settings.figure_dpi)
            front_matter, body = parse_front_matter(conv.markdown)
            manifest = build_manifest(
                source=source,
                source_format=suffix.lstrip("."),
                front_matter=front_matter,
                stats=conv.stats,
                ocr=ocr_info,
                warnings=warnings,
            )
            pack_textpack(out_path=bundle, stem=source.stem, body=body,
                          figure_dir=conv.figure_dir, manifest=manifest)
            return manifest
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def parse(self, ctx: ParseContext) -> ParseResult:
        rs = ctx.resolve(self.engine_name)
        source = rs.source_path
        if not (source.is_file() and source.suffix.lower() in SUPPORTED_SUFFIXES):
            raise ValueError(f"pdf2md parser does not support pending file: {ctx.file_path}")
        settings = Pdf2MdSettings.from_env()
        bundle = source.with_suffix(".textpack")

        if bundle.is_file() and _bundle_matches_source(bundle, source):
            logger.info("[pdf2md] reusing existing bundle %s", bundle.name)
            with zipfile.ZipFile(bundle) as z:
                manifest = json.loads(z.read(MANIFEST_NAME))
        else:
            try:
                manifest = await asyncio.to_thread(self._convert_to_bundle, source, bundle, settings)
            except (Pdf2MdConversionError, OcrError) as exc:
                raise ValueError(f"{source.name}: {exc}") from exc

        # Delegate the real parse to the native Markdown engine on the bundle.
        # parse_engine must name the delegate: NativeParserBase rejects a
        # directive for a different engine (guards against misrouted rows).
        from ontorag.parser.markdown.parser import NativeMarkdownParser

        delegate_ctx = dataclasses.replace(
            ctx,
            file_path=str(bundle),
            content_data={
                **(ctx.content_data if isinstance(ctx.content_data, dict) else {}),
                "parse_engine": PARSER_ENGINE_NATIVE,
                "source_file": bundle.name,
            },
        )
        result = await NativeMarkdownParser().parse(delegate_ctx)
        result.file_path = ctx.file_path
        result.parse_engine = self.engine_name
        result.canonical_source = bundle.name
        result.document_metadata = {
            "bibliographic": manifest.get("bibliographic", {}),
            "doc_type": manifest.get("doc_type"),
            "doc_scores": manifest.get("doc_scores", {}),
            "ocr": manifest.get("ocr"),
            "converter": manifest.get("converter", {}),
        }
        return result
```

Notes for the implementer: `ctx.resolve()` calls `ctx.rag._resolve_source_file_for_parser(file_path, ...)`; the test double returns the path unchanged, so an absolute test path resolves directly. `NativeMarkdownParser.validate_source` requires the `.textpack` suffix — satisfied. `OcrResult` is re-exported from `ocr.py` for the test's monkeypatch.

- [x] **Step 4: Run to verify pass** — `./scripts/test.sh tests/parser/pdf2md/test_parser.py -v` (6 PASS).

- [x] **Step 5: One real end-to-end conversion (no mocks)**

Append to `test_parser.py`:

```python
async def test_real_conversion_of_synthesised_pdf(text_pdf: Path, archived):
    result = await Pdf2MdParser().parse(ParseContext(_FakeRag(), "doc-real", str(text_pdf), {"parse_engine": "pdf2md"}))
    assert result.canonical_source == "book.textpack"
    blocks = Path(result.blocks_path).read_text(encoding="utf-8")
    assert "Body text of the page" in blocks
```

Run; if pdf2md classifies the two-page synthetic PDF oddly that is fine — the assertion is only that body text survived into the sidecar.

- [x] **Step 6: Commit** — `git commit -m "feat(pdf2md): Pdf2MdParser orchestrates OCR/convert/pack and delegates to the Markdown engine"`.

---

### Task 8: Re-scan skip for converted originals

**Files:**
- Modify: `ontorag/api/routers/document_routes.py:2971-2985` (`_ScanFileClass`), `classify_scan_file` (~3005-3060), the caller branch after `PROCESSED` (~3843)
- Test: `tests/api/routes/test_scan_classification_exits.py` (append)

**Interfaces:**
- Produces: `_ScanFileClass.CONVERTED_SOURCE = "converted_source"`; `classify_scan_file` returns it when the derived `<stem>.textpack` key resolves to a unique, non-FAILED document whose `metadata.source_file_original == file_path.name`.
- Consumes: `resolve_doc_source_strict`, `doc_status_field`.

- [x] **Step 1: Write the failing test** (append; reuse the module's `_ResolverDocStatus` and row helpers — read lines 45-140 of the file for `_row(...)`/rag construction helpers and use the same ones):

```python
def test_converted_original_is_skipped_without_archive():
    """A source whose <stem>.textpack document exists is CONVERTED_SOURCE:
    not enqueued, not archived, not deleted (the original stays put)."""
    doc_id = f"doc-{uuid4().hex}"
    row = _row(doc_id, status=DocStatus.PROCESSED, file_path="book.textpack",
               metadata={"source_file": "book.textpack", "source_file_original": "book.pdf"})
    doc_status = _ResolverDocStatus(
        {"book.pdf": SourceAbsent(), "book.textpack": SourceUnique(doc_id=doc_id, doc=row)},
        rows={doc_id: row},
    )
    rag = SimpleNamespace(doc_status=doc_status, workspace="")
    decision = asyncio.run(classify_scan_file(rag, Path("/in/book.pdf"), "book.pdf"))
    assert decision.kind is _ScanFileClass.CONVERTED_SOURCE
    assert decision.doc_id == doc_id


def test_textpack_document_with_different_original_does_not_claim_this_file():
    doc_id = f"doc-{uuid4().hex}"
    row = _row(doc_id, status=DocStatus.PROCESSED, file_path="book.textpack",
               metadata={"source_file": "book.textpack", "source_file_original": "book.epub"})
    doc_status = _ResolverDocStatus(
        {"book.pdf": SourceAbsent(), "book.textpack": SourceUnique(doc_id=doc_id, doc=row)},
        rows={doc_id: row},
    )
    rag = SimpleNamespace(doc_status=doc_status, workspace="")
    decision = asyncio.run(classify_scan_file(rag, Path("/in/book.pdf"), "book.pdf"))
    assert decision.kind is _ScanFileClass.CLAIMED_NEW
```

If the module has no `_row` helper, build the row as `DocProcessingStatus(content_summary="", content_length=0, file_path=..., status=..., created_at="", updated_at="", metadata=...)` — check the dataclass's required fields at `ontorag/base.py:1044`.

- [x] **Step 2: Run to verify failure** — `AttributeError: CONVERTED_SOURCE`.

- [x] **Step 3: Implement**

Enum: add `CONVERTED_SOURCE = "converted_source"` after `ALIAS_DUPLICATE`, and update the class docstring ("nine ... exits").

In `classify_scan_file`, replace

```python
    resolution = await rag.doc_status.resolve_doc_source_strict(canonical_source_key)
    if isinstance(resolution, SourceAbsent):
        return _ScanFileDecision(_ScanFileClass.CLAIMED_NEW)
```

with

```python
    resolution = await rag.doc_status.resolve_doc_source_strict(canonical_source_key)
    if isinstance(resolution, SourceAbsent):
        converted = await _converted_source_owner(rag, file_path, canonical_source_key)
        if converted is not None:
            return _ScanFileDecision(_ScanFileClass.CONVERTED_SOURCE, doc_id=converted)
        return _ScanFileDecision(_ScanFileClass.CLAIMED_NEW)
```

and add above `classify_scan_file`:

```python
async def _converted_source_owner(
    rag: OntoRAG, file_path: Path, canonical_source_key: str
) -> str | None:
    """Doc id of the ``<stem>.textpack`` document a converter engine (pdf2md)
    produced from THIS file, else None.

    Converter engines re-point the document of record to the generated bundle
    (``ParseResult.canonical_source``), so the original's own canonical key is
    absent from doc_status. The bundle name is deterministic, so one extra
    ``resolve_doc_source_strict`` lookup finds it; ``metadata.source_file_original``
    must equal this physical basename so ``book.epub`` cannot claim ``book.pdf``.
    """
    derived = f"{Path(canonical_source_key).stem}.textpack"
    if derived == canonical_source_key:
        return None
    resolution = await rag.doc_status.resolve_doc_source_strict(derived)
    if not isinstance(resolution, SourceUnique):
        return None
    if get_doc_status_value(resolution.doc) == DocStatus.FAILED.value:
        return None
    metadata = doc_status_field(resolution.doc, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return None
    if metadata.get("source_file_original") != file_path.name:
        return None
    return resolution.doc_id
```

(`doc_status_field` is in `ontorag.utils_pipeline`; import it if the module does not already.)

Caller branch, after the `PROCESSED` block:

```python
                if decision.kind is _ScanFileClass.CONVERTED_SOURCE:
                    # A converter engine already turned this file into its
                    # .textpack document of record. The original stays in
                    # place by design (never archived, never enqueued).
                    processed_count += 1
                    reporter.count(_ScanFileClass.CONVERTED_SOURCE.value)
                    reporter.sample("converted_source", filename)
                    continue
```

- [x] **Step 4: Run to verify pass** — `./scripts/test.sh tests/api/routes/test_scan_classification_exits.py -v`; also `./scripts/test.sh tests/api -q`.

- [x] **Step 5: Commit** — `git commit -m "feat(scan): skip originals already converted to a .textpack document"`.

---

### Task 9: WebUI — bibliographic title in the document list

**Files:**
- Modify: `ontorag_webui/src/features/DocumentManager.tsx:89-110` (`getDisplayFileName`) and the two `{doc.file_path}` cells (~1693, ~1706)
- Test: `ontorag_webui/src/features/documentDisplayName.test.ts`

- [x] **Step 1: Extract the helper into its own module and write the failing test**

Create `ontorag_webui/src/features/documentDisplayName.ts`:

```ts
import type { DocStatusResponse } from '@/api/ontorag'

export const bibliographicTitle = (doc: DocStatusResponse): string | null => {
  const title = doc.metadata?.bibliographic?.title
  return typeof title === 'string' && title.trim() !== '' ? title.trim() : null
}

export const bibliographicAuthors = (doc: DocStatusResponse): string | null => {
  const authors = doc.metadata?.bibliographic?.authors
  if (!Array.isArray(authors)) return null
  const names = authors.filter((a): a is string => typeof a === 'string' && a.trim() !== '')
  return names.length ? names.join(', ') : null
}
```

`documentDisplayName.test.ts`:

```ts
import { describe, expect, test } from 'bun:test'
import { bibliographicAuthors, bibliographicTitle } from './documentDisplayName'

const base = { id: 'd1', file_path: 'book.textpack' } as any

describe('bibliographic display', () => {
  test('title from metadata', () => {
    expect(bibliographicTitle({ ...base, metadata: { bibliographic: { title: ' T ' } } })).toBe('T')
  })
  test('no title -> null', () => {
    expect(bibliographicTitle(base)).toBeNull()
    expect(bibliographicTitle({ ...base, metadata: { bibliographic: { title: '' } } })).toBeNull()
  })
  test('authors joined', () => {
    expect(bibliographicAuthors({ ...base, metadata: { bibliographic: { authors: ['A', 'B'] } } })).toBe('A, B')
    expect(bibliographicAuthors(base)).toBeNull()
  })
})
```

- [x] **Step 2: Run to verify failure** — `cd ontorag_webui && bun test documentDisplayName` → module not found.

- [x] **Step 3: Implement** — create the module above; in `DocumentManager.tsx` import both helpers and, in each of the two cells that render `{doc.file_path}`, render:

```tsx
{bibliographicTitle(doc) ?? doc.file_path}
{bibliographicAuthors(doc) && (
  <div className="text-xs text-muted-foreground truncate">{bibliographicAuthors(doc)}</div>
)}
```

keeping the existing `title={doc.file_path}` tooltip so the file name remains discoverable.

- [x] **Step 4: Verify** — `bun test` (all green), `bun run lint`, `bun run build`.

- [x] **Step 5: Commit** — `git commit -m "feat(webui): show bibliographic title and authors for converted documents"`.

---

### Task 10: Environment, Docker, documentation

**Files:**
- Modify: `env.example:353` (after `ONTORAG_PARSER=...`), `env.docker-compose-full:245`
- Modify: `Dockerfile:125` (runtime apt line)
- Modify: `AGENTS.md`, `README.md`, `docs/FileProcessingPipeline.md` (+ `-zh.md`), `docs/OntoRAGSidecarFormat.md` (+ `-zh.md`), `docs/ThirdPartyParser.md`, `docs/GraphAndRagArchitecture.md` §5.4

- [x] **Step 1: env.example / env.docker-compose-full** — insert after the `ONTORAG_PARSER=` line in both files:

```bash

### pdf2md engine (optional; `pip install 'ontorag[pdf2md]'`, PyMuPDF is AGPL-3.0)
### Converts PDF / EPUB / DOCX / DOC / ODT / RTF to a Markdown-canonical
### <name>.textpack that becomes the document of record (catalogued, archived);
### the original file is never moved. Route it per suffix, e.g.:
###   ONTORAG_PARSER=pdf:pdf2md-iteP,epub:pdf2md-iteP,doc:pdf2md-iteP,*:native-teP,*:legacy-R
### Keep docx on native unless you prefer pdf2md's DOCX reader (no tracked changes).
### Image-only (scanned) PDFs are OCR'd IN PLACE with OCRmyPDF first: the
### original is copied to <folder>/__originals__/ (never overwritten) and the
### searchable PDF replaces it under the same name. Needs tesseract + ghostscript.
### PDF_OCR_ENGINE is OCRmyPDF's --ocr-engine; plugins (ocrmypdf-appleocr,
### ocrmypdf-easyocr, ocrmypdf-paddleocr) register their own engine names.
# MAX_PARALLEL_PARSE_PDF2MD=2
# PDF_OCR_ENGINE=tesseract
# PDF_OCR_LANGUAGES=eng
# PDF_OCR_DESKEW=true
# PDF_OCR_TIMEOUT=1800
# PDF2MD_ORIGINALS_DIRNAME=__originals__
# PDF2MD_SOFFICE=/opt/libreoffice/program/soffice
# PDF2MD_FIGURE_DPI=200
```

- [x] **Step 2: Dockerfile** — change line 125 to install the OCR/LibreOffice system packages in the full image only:

```dockerfile
    && apt-get install -y --no-install-recommends gosu libcairo2 \
        tesseract-ocr ghostscript libreoffice-core libreoffice-writer \
```

and add `--extra pdf2md` to the full image's `uv sync` line (find it with `grep -n "uv sync" Dockerfile`; leave `Dockerfile.lite` untouched).

- [x] **Step 3: Documentation**

- `AGENTS.md` *Module Layout*: after the `parser/` bullet add: ``- **parser/pdf2md/** *(OntoRAG fork addition)*: Markdown-canonical intake. `_pdf2md.py` is the vendored pdf2md converter (PyMuPDF; `[pdf2md]` extra), `census.py` text-layer census, `ocr.py` OCRmyPDF in place with `__originals__/` backup, `textpack.py` bundle + `pdf2md.json` manifest, `parser.py` orchestrates and delegates to the native Markdown engine. The generated `.textpack` becomes the document of record via `ParseResult.canonical_source`; originals are never moved. Design: `docs/superpowers/specs/2026-09-02-pdf2md-markdown-intake-design.md`.`` Also add `pdf2md` to the *Setup* extras list and the env vars to *Configuration*.
- `README.md`: one paragraph under the features/install section: install extra, what it does, the residency rule, the OCR behaviour, and the routing example.
- `docs/FileProcessingPipeline.md` §3: add **3.6 Using the pdf2md Engine** (and renumber 3.6/3.7 → 3.7/3.8): capabilities matrix row (`pdf epub docx doc odt rtf`), install, routing, OCR in place + `__originals__/`, residency, reuse of an existing bundle, the `pdf2md.json` catalog fields, failure table from spec §8, and the `docx` recommendation. Mirror the section in `-zh.md` (write it in Chinese; do not machine-translate).
- `docs/OntoRAGSidecarFormat.md` (+ `-zh.md`): new section **11. `.textpack` bundles produced by pdf2md** with the layout and manifest schema from spec §6.
- `docs/ThirdPartyParser.md`: under §2.1 Contract, document `ParseResult.canonical_source` / `document_metadata` and the re-point semantics (spec §5.2), noting the scan `CONVERTED_SOURCE` rule.
- `docs/GraphAndRagArchitecture.md` §5.4: one paragraph — `bibliographic` and `doc_type` are now recorded per document by pdf2md and are the intended document-level inputs for Plan B's classifier.

- [x] **Step 4: Verify docs build nothing (markdown) and env files parse** — `grep -n "PDF2MD\|PDF_OCR" env.example env.docker-compose-full`, `docker build --target builder -t ontorag-test . --quiet` optional if Docker is present.

- [x] **Step 5: Commit** — `git commit -m "docs(pdf2md): env, Docker and documentation for the Markdown intake engine"`.

---

### Task 11: Full verification

- [x] **Step 1:** `uv sync --extra api --extra test --extra offline-storage --extra offline-llm --extra pdf2md`
- [x] **Step 2:** `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check ontorag tests`
- [x] **Step 3:** `./scripts/test.sh tests` — expected: 0 failed; the new suites run (not skipped) because `pymupdf` is installed.
- [x] **Step 4:** `cd ontorag_webui && bun test && bun run lint && bun run build`
- [x] **Step 5:** Manual smoke, if a real PDF is at hand: `ONTORAG_PARSER=pdf:pdf2md-iteP,*:native-teP,*:legacy-R`, drop `book.pdf` in `inputs/`, run a scan, confirm `inputs/book.pdf` unchanged, `inputs/__parsed__/book.textpack` archived, `doc_status.file_path == "book.textpack"`, `metadata.bibliographic` populated, drawings analysed with `subject`/`ocr_text`; run a second scan and confirm `converted_source` in the scan counters with no re-enqueue.
- [x] **Step 6:** Commit anything outstanding; do not push (the branch push awaits the user's `gh auth refresh -s workflow`).

---

## Self-review

**Spec coverage:** §3 flow → Tasks 2, 4, 1, 3, 7; §4 components → Tasks 1–7; §5.1 → Task 5; §5.2 → Task 6; §5.3 → Task 8; §5.4 env → Tasks 7 (`from_env`) and 10; §6 → Task 3; §7 catalog → Tasks 6, 9; §8 failures → Tasks 4, 5, 7 (probe messages, `ValueError` surfacing, temp cleanup); §9 tests → each task; §10 docs → Task 10; §11/§12 → Task 5 (extra), Task 10 (licences in env/Docker comments).

**Placeholder scan:** none; every code step is complete. Two steps tell the implementer to confirm an accessor name / a routing detail against the code (Task 6 step 4 note, Task 5 step 5) — those are verification instructions, not gaps.

**Type consistency:** `ConversionResult(markdown, figure_dir, stats, stdout)` used identically in Tasks 1 and 7; `OcrResult(applied, backup, engine, languages)` in Tasks 4 and 7; `pack_textpack(out_path, stem, body, figure_dir, manifest)` and `build_manifest(source, source_format, front_matter, stats, ocr, warnings)` in Tasks 3 and 7; `ParseResult.canonical_source` / `document_metadata` in Tasks 6 and 7; `_ScanFileClass.CONVERTED_SOURCE` in Task 8 only.
