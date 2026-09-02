# pdf2md Markdown intake — design

**Status:** implemented 2026-09-02 on branch `sync/lightrag-1.5.7` (commits `5395606`..`1e65937`; plan `docs/superpowers/plans/2026-09-02-pdf2md-markdown-intake.md`). Deviations from this spec, all recorded in the plan/commits: the scan's derived-bundle lookup is made only for suffixes the `pdf2md` spec claims (§5.3); the vendored file carries a second edit (a Python 3.12-only f-string hoisted for the 3.10 target); `ruff format` excludes the vendored file.
**Scope:** OntoRAG fork (`machinarii/OntoRAG`), branch `sync/lightrag-1.5.7`.

## 1. Summary

OntoRAG gains a `pdf2md` parser engine that turns PDF, EPUB, DOCX, DOC, ODT and
RTF sources into a **Markdown-canonical `.textpack`** (Markdown + extracted
figures + a bibliographic manifest) and hands that bundle to the existing native
Markdown engine. The `.textpack` — not the original file — becomes the
document of record: it is what `doc_status.file_path` names, what is archived
to `__parsed__/`, and what citations point at. Originals are never moved by
OntoRAG.

Image-only (scanned) PDFs are OCR'd first with OCRmyPDF: the pre-OCR original
is backed up beside the file in `__originals__/`, and the searchable PDF
replaces it in place, so the operator's library is upgraded rather than
consumed.

Figures extracted by pdf2md flow through the VLM image analysis that already
returns `type` / `subject` / `ocr_text`, so the knowledge graph gains typed,
classification-ready image nodes from every ingested book, paper or deck.

The converter is the user's `pdf2md` (single-file build `pdf2md_all.py`,
~4.4k lines, PyMuPDF for PDF, stdlib for EPUB/DOCX, LibreOffice for DOC/ODT/RTF),
vendored verbatim.

## 2. Decisions recorded

| Question | Decision |
|---|---|
| Canonical document for a converted source | The Markdown `.textpack`. |
| Where pdf2md lives | In-tree, `ontorag/parser/pdf2md/`, optional extra `ontorag[pdf2md]` (PyMuPDF is AGPL-3.0; it is installed only by deployments that opt in). |
| Scanned PDFs | OCR them (OCRmyPDF); do not fall back to another engine. |
| OCR file handling | Back up the original elsewhere (`__originals__/` beside the file, subfolders preserved); place the OCR'd PDF in the original folder under the original name. |
| Residency after conversion | Source files (PDF/EPUB/…) stay in their folder and are never moved. Only the generated `.textpack` is enqueued and archived. Re-scans recognise converted sources and skip them. |
| EPUB / DOC / DOCX | Supported by the updated pdf2md (`structured.py`). `docx` stays routed to the built-in `native` engine by default; `docx:pdf2md` is an explicit opt-in. |
| Documentation | Updated in the same change (see §10). |
| YAGO classifier | Not wired (Plan B). `doc_type` and bibliographic metadata are recorded as its future inputs. |

Rejected alternatives: pdf2md as a parse artifact with the PDF canonical
(user chose Markdown-canonical); MinerU/PaddleOCR-VL/Chandra as the OCR path
(none produce a searchable PDF, and Chandra's model licence restricts
commercial use); a second `doc_status` row as a "conversion receipt" (the
pipeline treats a zero-chunk document as a failure, so it would need a new
terminal status — more contract change than the one field in §5.2).

## 3. Flow

Routing example: `ONTORAG_PARSER=pdf:pdf2md-iteP,epub:pdf2md-iteP,doc:pdf2md-iteP,*:native-teP,*:legacy-R`.

For a source `lib/book.pdf` the engine runs **in the parse worker** (own
`queue_group="pdf2md"`, concurrency `MAX_PARALLEL_PARSE_PDF2MD`, default 2), so
conversion and OCR inherit the pipeline's retries, FAILED semantics,
cancellation checks and concurrency control. Nothing heavy runs in the
upload/scan enqueue path.

1. **Reuse check.** If `lib/book.textpack` already exists and its manifest's
   `source.sha256` matches the current source bytes, skip to step 5.
2. **Text-layer census** (PDF only). PyMuPDF span count per page; a document
   whose pages are all glyphless is image-only.
3. **OCR** (PDF only, image-only). Copy `lib/book.pdf` to
   `lib/__originals__/book.pdf` (no overwrite: an existing backup is kept and
   the copy is skipped), run OCRmyPDF to a temp file in the same directory,
   `os.replace` it over `lib/book.pdf`. A recognizer is chosen through
   OCRmyPDF's own plugin mechanism (`PDF_OCR_ENGINE`; Tesseract default,
   AppleOCR/EasyOCR/PaddleOCR plugins register themselves as engines).
4. **Convert.** pdf2md on the (text-layer) source with `figure_dir`,
   `artifacts` (for `doc_type`/`doc_scores`), `figure_vlm` never. DOC/ODT/RTF
   are first converted to DOCX by LibreOffice inside pdf2md. Output: Markdown
   with YAML front matter, figure files, `stats.json`.
5. **Pack** `lib/book.textpack` (§6). Front matter is parsed into the manifest
   and removed from the body; figures are copied in deduplicated by content
   hash; links are rewritten to the deduplicated names.
6. **Delegate.** The native Markdown parser parses the `.textpack` exactly as
   an uploaded `.textpack`: `.blocks.jsonl`, `drawings.json`, figures to the
   VLM. The engine returns that `ParseResult` with `canonical_source` set
   (§5.2).

## 4. Components

```
ontorag/parser/pdf2md/
├── __init__.py       # register_parser(ParserSpec(...)) via registry, availability probe
├── _pdf2md.py        # VENDORED pdf2md_all.py, verbatim except: main() split into
│                     #   convert(...) + a thin CLI main(); no other edits. Header records
│                     #   the upstream version/date for future drops.
├── convert.py        # convert_source(path, work_dir, *, doc_type=None, soffice=None) -> ConversionResult
│                     #   (markdown text, front_matter dict, figure paths, stats dict, warnings)
├── census.py         # pdf_text_layer_census(path) -> Census(pages, text_pages, image_only: bool)
├── ocr.py            # ocr_in_place(path, *, originals_dir, engine, languages, ...) -> OcrResult
│                     #   backup, temp output, atomic replace; never touches the original on failure
├── textpack.py       # parse_front_matter(md) -> (meta, body); pack_textpack(...) -> Path;
│                     #   manifest build; figure dedupe + link rewrite
├── probe.py          # check_pdf2md_available(), check_ocr_available(), check_soffice_available()
└── parser.py         # Pdf2MdParser(NativeParserBase): orchestrates §3, delegates to
                      #   parser.markdown.parser.NativeMarkdownParser
```

Pipeline / API touch points (small, each with a test):

- `ontorag/parser/base.py` — `ParseResult.canonical_source: str | None`,
  `ParseResult.document_metadata: dict | None`.
- `ontorag/pipeline.py` parse stage — apply `canonical_source` and
  `document_metadata` (§5.2); `_resolve_source_file_for_parser` prefers the
  canonical source when present.
- `ontorag/api/routers/document_routes.py` scan classification — skip a source
  whose canonical basename matches a non-FAILED document's
  `metadata.source_file_original` (§5.3).
- `ontorag/parser/registry.py` — no change; the engine registers itself from
  `ontorag/parser/pdf2md/__init__.py`, imported by the built-in registration
  list so it is present without an entry point (it is part of the package, not
  a third-party plugin), but `endpoint_configured` reports the extra's
  availability.
- `ontorag_webui/src/features/DocumentManager.tsx` — list row shows
  `metadata.bibliographic.title` (fallback: file name) with authors beneath.
- `pyproject.toml` — `[project.optional-dependencies] pdf2md = ["pymupdf>=1.24", "ocrmypdf>=16"]`.

## 5. Contracts

### 5.1 ParserSpec

```python
ParserSpec(
    engine_name="pdf2md",
    impl="ontorag.parser.pdf2md.parser:Pdf2MdParser",
    suffixes=frozenset({"pdf", "epub", "docx", "doc", "odt", "rtf"}),
    queue_group="pdf2md",
    concurrency=int(os.getenv("MAX_PARALLEL_PARSE_PDF2MD", "2")),
    endpoint_configured=check_pdf2md_available,      # pymupdf importable
    endpoint_requirement=lambda: "pip install 'ontorag[pdf2md]'",
)
```

Routing treats a missing extra exactly like an unconfigured external engine:
the rule is skipped and the file falls to the next rule. `docx` is in the
suffix set but the shipped `env.example` routes `docx` to `native`.

### 5.2 `ParseResult.canonical_source` and `document_metadata`

New optional fields. When the parse stage receives a result with
`canonical_source` set:

- `doc_status.file_path` ← `canonical_source` basename (e.g. `book.textpack`);
  `doc_status.metadata.source_file` ← the same (this is the field the worker
  and archive resolver read); `doc_status.metadata.source_file_original` ← the
  enqueued basename (`book.pdf`).
- Both new metadata keys join `_DOC_STATUS_METADATA_CARRY_OVER_KEYS` so they
  survive ANALYZING → PROCESSING → PROCESSED.
- `document_metadata` is merged into `doc_status.metadata` (keys:
  `bibliographic`, `doc_type`, `doc_scores`, `ocr`, `converter`).
- `archive_source_after_full_docs_sync` therefore moves the `.textpack`; the
  original is untouched because nothing references it as the source any more.
- `full_docs.content_data.file_path` / chunk `file_path` (citations) use the
  canonical name, as they are written after the parse stage.

Engines that do not set the field are unaffected; `to_dict` omits it when
`None` (matches the existing "no spurious keys" rule).

### 5.3 Re-scan skip rule

In `run_scanning_process` classification, after the existing
canonical-basename lookup: a file whose canonical basename equals
`metadata.source_file_original` of any document not in FAILED is classified as
already processed and is not enqueued (and, per the residency decision, not
moved). The lookup is one pass over the doc_status snapshot the scan already
holds, keyed by that field.

### 5.4 Environment

| Variable | Default | Meaning |
|---|---|---|
| `MAX_PARALLEL_PARSE_PDF2MD` | `2` | Worker count for the `pdf2md` queue group. |
| `PDF_OCR_ENGINE` | `tesseract` | OCRmyPDF `--ocr-engine` name; plugins register their own. |
| `PDF_OCR_LANGUAGES` | `eng` | Comma-separated Tesseract language codes. |
| `PDF_OCR_DESKEW` | `true` | OCRmyPDF `deskew`. |
| `PDF_OCR_TIMEOUT` | `1800` | Seconds; exceeded → FAILED, temp output removed. |
| `PDF2MD_ORIGINALS_DIRNAME` | `__originals__` | Backup directory name, created beside the source. |
| `PDF2MD_SOFFICE` | (PATH lookup) | LibreOffice binary for DOC/ODT/RTF. |
| `PDF2MD_FIGURE_DPI` | `200` | Figure crop resolution for PDF. |
| `PDF_PASSWORD` | existing | Reused for encrypted PDFs. |

## 6. `.textpack` contract

```
book.textpack (zip)
├── book.md          # body; front matter removed; figures as ![caption](figs/<sha12>.png)
├── figs/<sha12>.<ext>
└── pdf2md.json
```

`pdf2md.json`:

```json
{
  "schema": 1,
  "source": {"name": "book.pdf", "format": "pdf", "sha256": "…", "bytes": 123},
  "bibliographic": {"title": "…", "authors": ["…"], "year": 2022, "publisher": "…",
                    "edition": "…", "isbn": ["…"], "arxiv": "…", "language": "en"},
  "doc_type": "book", "doc_scores": {"book": 14.0, "paper": 3.0},
  "pages": 575, "figures": 22, "tables": 5,
  "ocr": null | {"applied": true, "engine": "tesseract", "languages": "eng",
                 "original_backup": "__originals__/book.pdf", "ocrmypdf": "16.x"},
  "converter": {"pdf2md": "<upstream build id>", "ontorag": "1.5.7"},
  "warnings": ["…"]
}
```

Only keys pdf2md actually found appear under `bibliographic`. The native
Markdown parser ignores non-`.md` entries, so the bundle is valid for it
unchanged; a hand-made `.textpack` without the manifest is still accepted —
the manifest feeds the catalog (§7), not the parser. Zip-bomb and image
budgets are the markdown engine's existing `_TEXTPACK_MAX_*` and
`NATIVE_MD_IMAGE_*` guards.

## 7. Catalog

`doc_status.metadata` gains `bibliographic`, `doc_type`, `doc_scores`, `ocr`,
`converter`, `source_file_original`. `/documents` already returns `metadata`;
the WebUI details view already renders it. The list row shows the
bibliographic title with authors beneath, falling back to the file name.

## 8. Failure semantics

| Stage | Condition | Outcome |
|---|---|---|
| Availability | `pymupdf` not importable | Engine unavailable; routing skips it; startup warns once. Never a runtime crash. |
| OCR prerequisites | `ocrmypdf` / `tesseract` / `gs` missing | Startup probe warns once; an image-only PDF is FAILED with the probe's message (text-layer PDFs still convert). |
| OCR | OCRmyPDF error or timeout | Temp output removed; source untouched; backup already made and kept; FAILED with OCRmyPDF's message. Scan retry re-runs OCR (backup step is a no-op). |
| OCR | output still has no text layer | FAILED `"OCR produced no text layer"`; OCR'd file kept in place, backup kept. |
| LibreOffice | `soffice` missing / timeout | FAILED with pdf2md's message (`"needs LibreOffice (soffice) on PATH"`). |
| pdf2md | any exception / refusal | FAILED with the message; nothing written outside the temp dir. |
| Encrypted PDF | wrong/missing `PDF_PASSWORD` | FAILED, same wording as the legacy engine. |
| Delegated parse | any | Native engine semantics; the `.textpack` stays so a retry reuses it (§3 step 1). |
| Cancellation | between stages | Worker cancellation checks; a cancelled OCR removes its temp output. |

## 9. Testing

- **Unit (no real documents):** front-matter parse/strip incl. multi-value
  `isbn`/`authors`; manifest build; `pack_textpack` round-trip through the
  markdown parser's `_open_textpack`; figure dedupe and link rewrite;
  text-layer census on PyMuPDF-synthesised text and image-only pages;
  `ocr_in_place` with OCRmyPDF mocked (backup created once, no overwrite,
  atomic replace, temp removed on failure, original untouched on failure).
- **Contract:** ParserSpec registration and availability gating; routing
  `pdf:pdf2md` claims `.pdf` and falls through when unavailable;
  `canonical_source` application (`file_path`, `source_file`,
  `source_file_original`, carry-over across transitions); archive moves the
  `.textpack` and leaves the source; re-scan skip rule; `document_metadata`
  merge.
- **Golden:** the generated fixtures from pdf2md's suite that are
  redistributable (`prd.docx`, `prd.doc`→ via LibreOffice when present,
  `minibook.epub`, `gen-whitepaper.epub`, `deck.pdf`, `prd.pdf`) with the
  invariants ported from `test_books.py`. Real books/papers run as
  `integration` against the operator's files.
- **Gating:** `pytest.importorskip("pymupdf")`; OCR and LibreOffice tests skip
  via the probes, mirroring the libcairo pattern.

## 10. Documentation (same change)

`AGENTS.md` (module layout, extra, env), `README.md` (install + feature
paragraph), `docs/FileProcessingPipeline.md` + `-zh.md` (engine, OCR
behaviour, `__originals__/`, routing example, residency), `docs/OntoRAGSidecarFormat.md`
+ `-zh.md` (`.textpack` manifest), `docs/ThirdPartyParser.md`
(`canonical_source` / `document_metadata` contract), `docs/GraphAndRagArchitecture.md`
§5.4 (bibliographic/doc_type as Plan B inputs), `env.example` +
`env.docker-compose-full` (§5.4 variables), `Dockerfile` (tesseract, ghostscript,
libreoffice-core in the full image; not in `Dockerfile.lite`), `pyproject.toml`.

## 11. Known gaps and follow-ups

- pdf2md's structured path has not been exercised on real-world EPUBs with
  images upstream; the packer's content-hash dedupe covers the known
  same-filename collision.
- pdf2md's DOCX reader ignores tracked changes, comments and text boxes; the
  built-in `native` DOCX engine remains the default for `.docx`.
- Bibliographic metadata for a scanned PDF depends on OCR quality of the
  front matter; `doc_type` still classifies from structure.
- Plan B: `DocumentClassifier` over `bibliographic.title + doc_type + body`
  and per-image `name + subject + ocr_text + description`; roll-up policy
  for image classes into the document remains open.
- Alternative recognizers with better accuracy than Tesseract (PaddleOCR
  plugin, EasyOCR plugin, AppleOCR on macOS) are configuration, not code.

## 12. Dependencies and licensing

| Dependency | Licence | Where |
|---|---|---|
| PyMuPDF | AGPL-3.0 (or commercial) | extra `[pdf2md]` only |
| OCRmyPDF | MPL-2.0 | extra `[pdf2md]` |
| Tesseract, Ghostscript | Apache-2.0 / AGPL-3.0 | system packages, full Docker image |
| LibreOffice | MPL-2.0 | optional system package |
| pdf2md (vendored) | as provided by the author (this repository's owner) | in-tree |
