"""Tests for lightrag.taxonomy.vocabulary."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest_asyncio

from lightrag.kg.networkx_impl import NetworkXStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy.graph_loader import load_taxonomy_to_graph
from lightrag.taxonomy.parser import parse_ntriples_file
from lightrag.taxonomy.vocabulary import (
    count_descendants,
    select_working_vocabulary,
)
from lightrag.utils import EmbeddingFunc

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"


async def _noop_embed(texts: list[str], **_: object) -> np.ndarray:
    return np.zeros((len(texts), 4), dtype=np.float32)


_DUMMY_EMBED = EmbeddingFunc(embedding_dim=4, max_token_size=512, func=_noop_embed)


@pytest_asyncio.fixture
async def loaded_graph(tmp_path: Path):
    initialize_share_data()
    storage = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="vocabtest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=_DUMMY_EMBED,
    )
    await storage.initialize()
    yield storage
    await storage.finalize()
    finalize_share_data()


async def test_count_descendants_matches_known_counts(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    counts = await count_descendants(loaded_graph, [c.iri for c in classes])
    assert counts["http://schema.org/Thing"] == 7
    assert counts["http://schema.org/MedicalEntity"] == 4
    assert counts["http://schema.org/Drug"] == 2
    assert counts["http://schema.org/Medication"] == 0
    assert counts["http://schema.org/Vaccine"] == 0


async def test_select_vocab_returns_target_size_or_less(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph, [c.iri for c in classes], target_size=4
    )
    assert len(vocab) == 4


async def test_select_vocab_excludes_root_thing_by_default(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph, [c.iri for c in classes], target_size=10
    )
    assert "http://schema.org/Thing" not in vocab


async def test_select_vocab_honors_manual_exclusions(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph,
        [c.iri for c in classes],
        target_size=10,
        excluded_iris={"http://schema.org/Drug"},
    )
    assert "http://schema.org/Drug" not in vocab


async def test_select_vocab_orders_by_descendant_count_desc(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph, [c.iri for c in classes], target_size=3
    )
    assert vocab[0] == "http://schema.org/MedicalEntity"
    assert vocab[1] == "http://schema.org/Drug"
