"""Tests for scripts/yago/build_yago_taxonomy.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

from lightrag.utils import EmbeddingFunc

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "yago" / "build_yago_taxonomy.py"
FIXTURE = ROOT / "tests" / "fixtures" / "yago" / "mini_taxonomy.nt"


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "build_yago_taxonomy", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _embed(texts: list[str], **_: object) -> np.ndarray:
    out = np.zeros((len(texts), 16), dtype=np.float32)
    for i, t in enumerate(texts):
        for j, ch in enumerate(t.lower()[:16]):
            out[i, j] = ord(ch) / 255.0
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


async def test_build_taxonomy_populates_graph_and_index(tmp_path: Path):
    module = _load_script_module()
    embed = EmbeddingFunc(embedding_dim=16, max_token_size=8192, func=_embed)
    result = await module.build_taxonomy(
        files=[FIXTURE],
        working_dir=tmp_path,
        workspace="cli",
        embedding_func=embed,
        vocabulary_size=5,
    )
    assert result["classes_loaded"] == 8
    assert result["vocabulary_size"] == 5
    assert result["index_records"] == 5


async def test_build_taxonomy_is_idempotent(tmp_path: Path):
    module = _load_script_module()
    embed = EmbeddingFunc(embedding_dim=16, max_token_size=8192, func=_embed)
    first = await module.build_taxonomy(
        files=[FIXTURE],
        working_dir=tmp_path,
        workspace="cli",
        embedding_func=embed,
        vocabulary_size=5,
    )
    second = await module.build_taxonomy(
        files=[FIXTURE],
        working_dir=tmp_path,
        workspace="cli",
        embedding_func=embed,
        vocabulary_size=5,
    )
    assert first == second
