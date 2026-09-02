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
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ontorag.constants import (
    PARSED_DIR_NAME,
    PARSER_ENGINE_NATIVE,
    PARSER_ENGINE_PDF2MD,
)
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

__all__ = ["Pdf2MdParser", "Pdf2MdSettings", "OcrResult"]

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
            if raw is None:
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        def _int(name: str, default: int) -> int:
            raw = (os.getenv(name) or "").strip()
            try:
                return int(raw) if raw else default
            except ValueError:
                return default

        return cls(
            ocr_engine=(os.getenv("PDF_OCR_ENGINE") or "").strip() or "tesseract",
            ocr_languages=(os.getenv("PDF_OCR_LANGUAGES") or "").strip() or "eng",
            ocr_deskew=_bool("PDF_OCR_DESKEW", True),
            ocr_timeout=_int("PDF_OCR_TIMEOUT", 1800),
            originals_dirname=(os.getenv("PDF2MD_ORIGINALS_DIRNAME") or "").strip()
            or "__originals__",
            soffice=(os.getenv("PDF2MD_SOFFICE") or "").strip() or None,
            figure_dpi=_int("PDF2MD_FIGURE_DPI", 200),
        )


def _bundle_matches_source(bundle: Path, source: Path) -> bool:
    """True when ``bundle`` carries a manifest whose source sha256 equals ``source``'s."""
    try:
        with zipfile.ZipFile(bundle) as z:
            manifest = json.loads(z.read(MANIFEST_NAME))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return False
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return manifest.get("source", {}).get("sha256") == digest


class Pdf2MdParser(BaseParser):
    engine_name = PARSER_ENGINE_PDF2MD

    def _convert_to_bundle(
        self, source: Path, bundle: Path, settings: Pdf2MdSettings
    ) -> dict[str, Any]:
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
        work_dir = Path(
            tempfile.mkdtemp(prefix=f"{source.stem}.pdf2md-", dir=work_root)
        )
        try:
            conv = convert_source(
                source,
                work_dir,
                soffice=settings.soffice,
                figure_dpi=settings.figure_dpi,
            )
            front_matter, body = parse_front_matter(conv.markdown)
            manifest = build_manifest(
                source=source,
                source_format=suffix.lstrip("."),
                front_matter=front_matter,
                stats=conv.stats,
                ocr=ocr_info,
                warnings=warnings,
            )
            pack_textpack(
                out_path=bundle,
                stem=source.stem,
                body=body,
                figure_dir=conv.figure_dir,
                manifest=manifest,
            )
            return manifest
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def parse(self, ctx: ParseContext) -> ParseResult:
        rs = ctx.resolve(self.engine_name)
        source = rs.source_path
        if not (source.is_file() and source.suffix.lower() in SUPPORTED_SUFFIXES):
            raise ValueError(
                f"pdf2md parser does not support pending file: {ctx.file_path}"
            )
        settings = Pdf2MdSettings.from_env()
        bundle = source.with_suffix(".textpack")

        if bundle.is_file() and _bundle_matches_source(bundle, source):
            logger.info("[pdf2md] reusing existing bundle %s", bundle.name)
            with zipfile.ZipFile(bundle) as z:
                manifest = json.loads(z.read(MANIFEST_NAME))
        else:
            try:
                manifest = await asyncio.to_thread(
                    self._convert_to_bundle, source, bundle, settings
                )
            except (Pdf2MdConversionError, OcrError) as exc:
                raise ValueError(f"{source.name}: {exc}") from exc

        # Delegate the real parse to the native Markdown engine on the bundle.
        # parse_engine must name the delegate: NativeParserBase rejects a
        # directive for a different engine (guards against misrouted rows).
        from ontorag.parser.markdown.parser import NativeMarkdownParser

        base_content = ctx.content_data if isinstance(ctx.content_data, dict) else {}
        delegate_ctx = dataclasses.replace(
            ctx,
            file_path=str(bundle),
            content_data={
                **base_content,
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
