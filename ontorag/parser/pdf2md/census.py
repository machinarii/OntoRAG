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
