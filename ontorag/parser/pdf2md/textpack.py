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
BIBLIOGRAPHIC_KEYS = (
    "title",
    "edition",
    "authors",
    "publisher",
    "year",
    "isbn",
    "arxiv",
    "language",
)

_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_LINK_RE = re.compile(r"(!\[[^\]]*\]\()figs/([^)]+)(\))")


# Only these front-matter keys are numeric; ISBNs, arXiv ids and the like
# must stay strings (leading zeros, ISBN-10 check digit ``X``).
_INT_KEYS = frozenset({"year", "pages"})


def _scalar(raw: str, key: str | None = None) -> Any:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        return inner.replace('\\"', '"') if raw[0] == '"' else inner
    if key in _INT_KEYS and re.fullmatch(r"-?\d+", raw):
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
            meta[current].append(_scalar(line[4:], current))
            continue
        key, sep, value = line.partition(":")
        if not sep or line.startswith(" "):
            continue
        key = key.strip()
        current = key
        meta[key] = _scalar(value, key) if value.strip() else []
    return meta, markdown[m.end() :].lstrip("\n")


def build_manifest(
    *,
    source: Path,
    source_format: str,
    front_matter: dict[str, Any],
    stats: dict[str, Any],
    ocr: dict[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any]:
    from ontorag._version import __version__

    data = source.read_bytes()
    bibliographic = {
        k: front_matter[k]
        for k in BIBLIOGRAPHIC_KEYS
        if k in front_matter and front_matter[k] not in ("", [], None)
    }
    return {
        "schema": 1,
        "source": {
            "name": source.name,
            "format": source_format,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        },
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


def pack_textpack(
    *,
    out_path: Path,
    stem: str,
    body: str,
    figure_dir: Path | None,
    manifest: dict[str, Any],
) -> Path:
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
