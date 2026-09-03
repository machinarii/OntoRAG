# Lessons from paperless-ngx — design and plan

**Status:** implemented 2026-09-02 on branch `sync/lightrag-1.5.7` (§1 `5f4774f`, `e98ff4f`; §2 `8f50613`; §3 `763059f`, `e2973d5`, `c3e1823`). §4 deferred as listed.
**Source reviewed:** [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) (`dev`, GPL-3.0). **Nothing is copied**; OntoRAG is MIT, so only designs and configuration surfaces are borrowed.

## 1. OCR stage hardening (`ontorag/parser/pdf2md/ocr.py`, `probe.py`, `parser.py`)

Borrowed from `paperless/parsers/tesseract.py::construct_ocrmypdf_parameters` and its exception policy, and from `paperless/checks.py`.

**Settings** (`OcrSettings`, all from env via `Pdf2MdSettings.from_env`):

| Env | Default | OCRmyPDF effect |
|---|---|---|
| `PDF_OCR_MODE` | `auto` | `auto` → `--skip-text`; `force` → `--force-ocr` (rasterize everything); `redo` → `--redo-ocr` (replace a bad text layer, e.g. glyphless stamps). |
| `PDF_OCR_ROTATE_PAGES` / `PDF_OCR_ROTATE_PAGES_THRESHOLD` | `true` / `12` | `--rotate-pages --rotate-pages-threshold N` (2 aggressive … 15 conservative). |
| `PDF_OCR_CLEAN` | `none` | `clean` → `--clean`, `clean-final` → `--clean-final` (unpaper; `--clean` when mode is `redo`). |
| `PDF_OCR_OUTPUT_TYPE` | `pdf` | `--output-type pdf|pdfa|pdfa-1|pdfa-2|pdfa-3` (PDF/A rewrites more of the file; default stays `pdf` because we replace the original in place). |
| `PDF_OCR_MAX_IMAGE_MPIXELS` / `PDF_OCR_IMAGE_DPI` | unset | `--max-image-mpixels` / `--image-dpi`. |
| `PDF_OCR_USER_ARGS` | unset | Extra CLI arguments (JSON array or shell-split string), appended verbatim — any OCRmyPDF option without a code change. |
| existing: `PDF_OCR_ENGINE`, `PDF_OCR_LANGUAGES`, `PDF_OCR_DESKEW`, `PDF_OCR_TIMEOUT` | | `--deskew` is not passed in `redo` mode (OCRmyPDF forbids the combination). |

A `--sidecar` text file is always requested; its non-blank length is the primary "did OCR produce text" check (the page census stays as fallback).

**Exit-code policy** (`ocr_in_place`, one retry at most; exit codes from `ocrmypdf.exceptions.ExitCode`):

| Exit | Meaning | Policy |
|---|---|---|
| 6 `already_done_ocr` in `auto` mode | a text layer exists after all | retry once with `force` |
| 8 `encrypted_pdf` | | `OcrSkippedError("encrypted PDF")` — no OCR; source untouched |
| 2 `input_file` with "digital signature" in stderr | signed PDF | `OcrSkippedError("digitally signed PDF; OCR would invalidate the signature")` |
| 10 `pdfa_conversion_failed` with a PDF/A output type | | retry once with `--output-type pdf` |
| 3 `missing_dependency` | e.g. unpaper, gs | `OcrError` naming the tool |
| other non-zero | | `OcrError` with the stderr tail |

`OcrSkippedError` reaches `Pdf2MdParser`, which fails the document with the reason (an image-only PDF that cannot be OCR'd cannot be converted); a text-layer PDF never enters OCR.

**Startup checks** (`probe.py`, surfaced by `utils_api._warn_about_pdf2md_ocr()` next to the SVG warning, only when the extra is installed): binaries for the configured settings (`ocrmypdf`, `tesseract`, `gs`, plus `unpaper` when `PDF_OCR_CLEAN` ≠ `none`), enum validation of `mode` / `clean` / `output_type`, and `check_tesseract_languages()` against `tesseract --list-langs` so a missing language pack is reported once at boot instead of once per document.

## 2. Scan stability delay (`document_routes.py`, `config.py`)

Borrowed from `PAPERLESS_CONSUMER_STABILITY_DELAY`. New `SCAN_STABILITY_DELAY` (seconds, default `0` = off, validated non-negative at startup). During scan discovery, a file whose mtime is younger than the delay is **deferred**: counted as `unstable`, sampled, neither claimed nor archived — the next scan picks it up. Protects a watched folder from ingesting a file that is still being copied.

## 3. Classification capabilities for Plan B (`ontorag/taxonomy/`)

Borrowed from `paperless_ai` (`taxonomy.py`, `ai_classifier.py`, `matching.py`, `prompts/classification.j2`) and `documents/classifier.py`. Standalone like Plan A: nothing is wired into ingestion (Plan B remains gated by the coverage check in `GraphAndRagArchitecture.md` §5.8).

- **Neighbour-label candidates** (`neighbors.py`): `neighbor_class_candidates(query_text, *, document_vdb, classes_of, top_k=15)` retrieves the most similar already-classified documents and aggregates *their* class assignments weighted by similarity; `merge_candidates()` unions them with the class-index candidates. Plan B supplies `classes_of(doc_id)` from `doc_status.metadata`.
- **Prompt discipline** (`classifier.py`): candidates are "options, not requirements"; the document is labelled untrusted data whose instructions must not be followed; the model first suggests freely (`suggested_names`), then reconciles to candidates (`assignments`); returned IRIs are allow-listed (already the case).
- **Name reconciliation + unmatched channel**: `suggested_names` are matched to candidate labels exactly, then fuzzily (`difflib`, cutoff 0.8, one-to-one); matches fold into the assignments, the rest surface as `unmatched_names` on `classify_detailed()` — the raw material for vocabulary overlays instead of a silent `Uncategorized`. `classify()` keeps its `[{iri, score}]` contract.
- **Supervised prior** (`supervised.py`): `NaiveBayesClassPrior` — multinomial naive Bayes over unigram+bigram counts (numpy only), fitted on human-confirmed `(text, iris)` examples, JSON-persisted with a training fingerprint so it retrains only when the confirmed set changes. `DocumentClassifier` accepts it as `prior=`: its probabilities re-rank candidates, and when the top class clears `prior_skip_threshold` (0.9) the LLM call is skipped.

## 4. Deferred (recorded for later)

- Originals-vs-archive placement toggle (`PDF2MD_OCR_PLACEMENT=replace|beside`; paperless never modifies the original and serves the PDF/A archive copy).
- `FILENAME_FORMAT`-style templating for archived `.textpack`s (`{year}/{authors}/{title}`) from bibliographic metadata.
- Ingestion adapters: IMAP mail attachments (`paperless_mail`), barcode-separated batch splitting / ASNs, `SUBDIRS_AS_TAGS` (folder → label), pre/post-consume script hooks.
- Remote OCR engine returning a searchable PDF (Azure Document Intelligence pattern in `parsers/remote.py`), selectable via the same `endpoint_configured` mechanism.

## Plan (TDD, one commit per task)

1. `ocr.py`: `OcrSettings` fields, `_build_ocrmypdf_args`, `OcrCommandError(exit_code, stderr_tail)`, sidecar text, retry/skip policy; `parser.py` env + `OcrSkippedError` handling. Tests in `tests/parser/pdf2md/test_ocr.py` / `test_parser.py`.
2. `probe.py`: `validate_ocr_settings`, `check_tesseract_languages`, unpaper; `utils_api._warn_about_pdf2md_ocr`. Tests in `test_probe.py`.
3. `SCAN_STABILITY_DELAY`: `config.py` + `document_routes.py` discovery; test in `tests/api/routes/test_scan_streaming_batches.py`.
4. `taxonomy/neighbors.py` + tests.
5. `taxonomy/classifier.py` prompt, `suggested_names`, `classify_detailed`, prior hook + tests.
6. `taxonomy/supervised.py` + tests.
7. Docs: `env.example`, `env.docker-compose-full`, `FileProcessingPipeline` (en/zh) §3.8, `AGENTS.md`, `GraphAndRagArchitecture.md` §5.4/§5.8, this file's status.
8. Full verification.
