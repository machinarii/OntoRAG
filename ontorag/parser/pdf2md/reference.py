"""Locate the separately maintained pdf2md checkout without importing it."""

from __future__ import annotations

import os
from pathlib import Path

REPOSITORY_URL = "https://github.com/machinarii/pdf2md"


def repository_path() -> Path:
    configured = os.getenv("PDF2MD_REPO_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "external" / "pdf2md"


def reference_available() -> bool:
    root = repository_path()
    return all((root / name).is_file() for name in ("pdf2md_all.py", "structured.py"))
