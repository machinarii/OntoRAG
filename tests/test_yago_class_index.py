"""Tests for lightrag.taxonomy.class_index."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest_asyncio

from lightrag.kg.nano_vector_db_impl import NanoVectorDBStorage
from lightrag.kg.networkx_impl import NetworkXStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy.class_index import (
    build_class_index,
    retrieve_candidate_classes,
)
from lightrag.taxonomy.graph_loader import load_taxonomy_to_graph
from lightrag.taxonomy.parser import parse_ntriples_file
from lightrag.utils import EmbeddingFunc

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"

_DIM = 16


def _deterministic_embed_sync(texts: list[str]) -> np.ndarray:
    out = np.zeros((len(texts), _DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for word in t.lower().split():
            digest = hashlib.md5(word.encode()).digest()
            for j in range(_DIM):
                out[i, j] += digest[j % len(digest)]
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


async def _embed_async(texts: list[str], **_: object) -> np.ndarray:
    return _deterministic_embed_sync(texts)


@pytest_asyncio.fixture
async def storages(tmp_path: Path):
    initialize_share_data()
    embed = EmbeddingFunc(
        embedding_dim=_DIM,
        max_token_size=8192,
        func=_embed_async,
    )
    graph = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="idxtest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=embed,
    )
    await graph.initialize()
    vdb = NanoVectorDBStorage(
        namespace=NameSpace.VECTOR_STORE_YAGO_CLASSES,
        workspace="idxtest",
        global_config={
            "working_dir": str(tmp_path),
            "embedding_batch_num": 32,
            "vector_db_storage_cls_kwargs": {
                "cosine_better_than_threshold": 0.0,
            },
        },
        embedding_func=embed,
        meta_fields={"iri", "label", "content"},
    )
    await vdb.initialize()
    yield graph, vdb, embed
    await vdb.finalize()
    await graph.finalize()
    finalize_share_data()


async def test_build_index_populates_one_record_per_class(storages):
    graph, vdb, _embed = storages
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    iris = [c.iri for c in classes]
    await build_class_index(iris, graph, vdb)
    hits = await retrieve_candidate_classes("Drug substance medication", vdb, top_n=3)
    assert len(hits) <= 3
    top_iris = [h["iri"] for h in hits]
    assert "http://schema.org/Drug" in top_iris


async def test_retrieve_returns_iri_label_score_shape(storages):
    graph, vdb, _embed = storages
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    iris = [c.iri for c in classes]
    await build_class_index(iris, graph, vdb)
    hits = await retrieve_candidate_classes("hospital", vdb, top_n=2)
    assert len(hits) > 0
    for h in hits:
        assert set(h.keys()) >= {"iri", "label", "score"}
        assert isinstance(h["score"], float)


async def test_build_index_respects_iri_subset(storages):
    graph, vdb, _embed = storages
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    await build_class_index(
        [
            "http://schema.org/Drug",
            "http://schema.org/Hospital",
        ],
        graph,
        vdb,
    )
    hits = await retrieve_candidate_classes("Person", vdb, top_n=10)
    iris = [h["iri"] for h in hits]
    assert "http://schema.org/Person" not in iris
