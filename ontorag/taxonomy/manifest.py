"""SHA256 manifest for the YAGO 4.0 T-Box files committed alongside the
classifier.

Pins the exact bytes we parse so "YAGO 4.0" cannot silently drift to a
different snapshot upstream. The files live at /Users/jin/OntoRAG/yago/
and originate from the YAGO 4.0 release dated 2020-02-24, mirrored at
https://yago-knowledge.org/data/yago4/full/2020-02-24/.

The build CLI calls verify_yago_files() before parsing; tests bypass it
by calling build_taxonomy() directly with custom fixture paths.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

YAGO_VERSION = "yago-4.0-2020-02-24"
YAGO_DATA_DIR = Path(__file__).resolve().parents[2] / "yago"

PINNED_FILES: dict[str, str] = {
    "yago-wd-class.nt": "0b11dff027ad77d82b83bf4a241389760c2801ce6f3c92b77684d752abfa0670",
    "yago-wd-schema.nt": "1a5484f1402aebe9e1d07e3df8fd02421d0f7bc7dccf3012ba49f4f95610c90c",
    "yago-wd-shapes.nt": "05a542e176a96b32ee265bb5bf4e51d403779c399db7a0044c4571814475b729",
}

# Files Plan A actually parses. yago-wd-shapes.nt ships in the same release
# bundle but only carries SHACL constraints we don't consume; it's pinned in
# PINNED_FILES so checksum drift is detected, but not handed to the parser.
TAXONOMY_FILES: tuple[str, ...] = ("yago-wd-class.nt", "yago-wd-schema.nt")


class YagoFileChecksumError(RuntimeError):
    """Raised when a pinned YAGO file is missing or its bytes have drifted."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def default_taxonomy_paths(data_dir: Path = YAGO_DATA_DIR) -> list[Path]:
    """Paths the build CLI parses by default."""
    return [data_dir / name for name in TAXONOMY_FILES]


def verify_yago_files(data_dir: Path = YAGO_DATA_DIR) -> None:
    """Confirm every PINNED_FILES entry is present at `data_dir` and matches.

    Raises YagoFileChecksumError listing every missing/drifted file at once
    so the operator sees the full picture rather than fixing one at a time.
    """
    problems: list[str] = []
    for fname, expected in PINNED_FILES.items():
        p = data_dir / fname
        if not p.exists():
            problems.append(f"missing: {p}")
            continue
        actual = sha256_of(p)
        if actual != expected:
            problems.append(
                f"checksum drift on {fname}: expected {expected}, got {actual}"
            )
    if problems:
        raise YagoFileChecksumError(
            f"YAGO file verification failed for {data_dir}: "
            + "; ".join(problems)
            + ". See scripts/yago/fetch_yago.sh for the canonical download URLs."
        )
