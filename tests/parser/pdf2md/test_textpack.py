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
        source=src,
        source_format="pdf",
        front_matter=meta,
        stats={
            "doc_type": "book",
            "doc_scores": {"book": 14.0},
            "pages": 575,
            "figure_regions": 2,
            "tables": 5,
        },
        ocr=None,
        warnings=[],
    )
    assert manifest["schema"] == 1
    assert manifest["source"]["name"] == "book.pdf"
    assert manifest["source"]["sha256"] == hashlib.sha256(b"%PDF-1.4 fake").hexdigest()
    assert manifest["bibliographic"] == {
        "title": "The Cognitive Structure of Emotions",
        "edition": "Second Edition",
        "authors": ["Andrew Ortony", "Gerald Clore"],
        "publisher": "Cambridge University Press",
        "year": 2022,
        "isbn": ["9781108844246", "9781108934053"],
    }
    assert "arxiv" not in manifest["bibliographic"]
    assert manifest["doc_type"] == "book" and manifest["pages"] == 575
    assert manifest["ocr"] is None


def test_pack_textpack_dedupes_figures_and_rewrites_links(tmp_path: Path):
    figs = tmp_path / "figs"
    figs.mkdir()
    png = b"\x89PNG\r\n\x1a\nsame-bytes"
    (figs / "fig-p012-340.png").write_bytes(png)
    (figs / "fig-p099-001.png").write_bytes(png)  # identical content, other name
    _, body = parse_front_matter(_MD)
    out = pack_textpack(
        out_path=tmp_path / "book.textpack",
        stem="book",
        body=body,
        figure_dir=figs,
        manifest={"schema": 1},
    )
    with zipfile.ZipFile(out) as z:
        names = sorted(z.namelist())
        sha12 = hashlib.sha256(png).hexdigest()[:12]
        assert names == ["book.md", f"figs/{sha12}.png", MANIFEST_NAME]
        md = z.read("book.md").decode("utf-8")
        assert md.count(f"](figs/{sha12}.png)") == 2
        assert "fig-p012" not in md
        assert json.loads(z.read(MANIFEST_NAME))["schema"] == 1


def test_pack_textpack_round_trips_through_markdown_parser(tmp_path: Path):
    out = pack_textpack(
        out_path=tmp_path / "book.textpack",
        stem="book",
        body="# T\n\nbody\n",
        figure_dir=None,
        manifest={"schema": 1},
    )
    parser = NativeMarkdownParser()
    md_text, bundle_root = parser._open_textpack(out, tmp_path / "unpack")
    assert md_text == "# T\n\nbody\n"
    assert (bundle_root / MANIFEST_NAME).is_file()
