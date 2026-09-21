# Referenced pdf2md converter

OntoRAG uses [machinarii/pdf2md](https://github.com/machinarii/pdf2md) as a
separate codebase. `external/pdf2md` is a Git submodule: OntoRAG records its
commit, without copying the converter source into the Python package.

## Source checkout

From the OntoRAG root:

```bash
git submodule update --init external/pdf2md
uv sync --extra pdf2md
```

The initial reference is `de632200abeb8813b347a23aa99ac20ae07ed362`.
`git submodule status external/pdf2md` shows the current pin. The extra installs
PyMuPDF and OCRmyPDF; PyMuPDF's AGPL licensing still applies. Scanned PDFs need
Tesseract and Ghostscript, and legacy office documents need LibreOffice.

## Installed packages and containers

Wheels and source distributions do not bundle the upstream converter. Clone
it separately and configure its location, using the same Python environment
as OntoRAG for the dependencies:

```bash
git clone https://github.com/machinarii/pdf2md /opt/pdf2md
git -C /opt/pdf2md checkout de632200abeb8813b347a23aa99ac20ae07ed362
export PDF2MD_REPO_PATH=/opt/pdf2md
```

For containers, initialize the submodule before building from source, or mount
the separate checkout and set `PDF2MD_REPO_PATH` to its container path. An
explicit path overrides the submodule location. Missing checkouts make the
engine unavailable and produce setup instructions during routing validation.
There are no runtime downloads.

## Adapter behavior

OntoRAG keeps the text census, OCR backups, parser routing, `.textpack` creation,
and native Markdown parsing. `convert.py` launches the upstream CLI in a
separate process using OntoRAG's Python interpreter. OCR remains managed by
OntoRAG and figure descriptions remain with its VLM role. Conversion has a
600-second timeout and surfaces upstream errors as document failures.

The small `_worker.py` shim sets the figure rendering default from
`PDF2MD_FIGURE_DPI` (upstream has no corresponding CLI flag), then invokes
upstream's `main()`. This adjustment only affects that conversion process.
The manifest records the upstream-reported version and repository URL. Existing
bundles with matching source hashes remain reusable and retain their original
provenance; this change does not force document reprocessing.

## Updating the reference

Check out the desired upstream commit inside `external/pdf2md`, run
`./scripts/test.sh tests/parser/pdf2md`, and commit the changed gitlink in
OntoRAG. Review CLI and rendering-signature compatibility before advancing the
pin. CI initializes submodules and runs these tests with the pdf2md extra.

The September 2 intake design and implementation plan document the original
vendored implementation; this reference replaces that part of the design.
