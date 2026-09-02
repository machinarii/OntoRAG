"""pdf2md engine: PDF/EPUB/DOCX/DOC/ODT/RTF -> Markdown-canonical .textpack.

Import-cheap by design: the vendored converter (PyMuPDF, AGPL) is imported
only by ``convert.py`` when a document is actually converted. See
docs/superpowers/specs/2026-09-02-pdf2md-markdown-intake-design.md.
"""
