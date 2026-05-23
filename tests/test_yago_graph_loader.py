"""Tests for lightrag.taxonomy.graph_loader."""

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
from lightrag.taxonomy.constants import YAGO_NODE_ENTITY_TYPE
from lightrag.taxonomy.graph_loader import (
    SUBCLASS_OF_EDGE_TYPE,
    load_taxonomy_to_graph,
    walk_ancestors,
)
from lightrag.taxonomy.parser import parse_ntriples_file
from lightrag.utils import EmbeddingFunc

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"


async def _noop_embed(texts: list[str], **_: object) -> np.ndarray:
    return np.zeros((len(texts), 4), dtype=np.float32)


_DUMMY_EMBED = EmbeddingFunc(embedding_dim=4, max_token_size=512, func=_noop_embed)


@pytest_asyncio.fixture
async def graph_storage(tmp_path: Path):
    initialize_share_data()
    storage = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="yagotest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=_DUMMY_EMBED,
    )
    await storage.initialize()
    yield storage
    await storage.finalize()
    finalize_share_data()


async def test_loads_every_class_as_a_node(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    for c in classes:
        node = await graph_storage.get_node(c.iri)
        assert node is not None, f"missing node {c.iri}"
        assert node["entity_type"] == YAGO_NODE_ENTITY_TYPE
        assert node["label"] == c.label


async def test_subclass_edges_have_correct_type(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    edge = await graph_storage.get_edge(
        "http://schema.org/Drug", "http://schema.org/MedicalEntity"
    )
    assert edge is not None
    assert edge["relation_type"] == SUBCLASS_OF_EDGE_TYPE
    assert edge["child_iri"] == "http://schema.org/Drug"


async def test_multi_parent_class_has_both_parent_edges(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    for parent in (
        "http://schema.org/Organization",
        "http://schema.org/MedicalEntity",
    ):
        edge = await graph_storage.get_edge(
            "http://schema.org/Hospital", parent
        )
        assert edge is not None, f"missing Hospital -> {parent}"
        assert edge["child_iri"] == "http://schema.org/Hospital"


async def test_walk_ancestors_returns_leaf_to_root_path(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    path = await walk_ancestors(
        "http://schema.org/Medication", graph_storage, max_depth=10
    )
    assert path == [
        "http://schema.org/Medication",
        "http://schema.org/Drug",
        "http://schema.org/MedicalEntity",
        "http://schema.org/Thing",
    ]


async def test_walk_ancestors_respects_max_depth(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    path = await walk_ancestors(
        "http://schema.org/Medication", graph_storage, max_depth=3
    )
    assert path == [
        "http://schema.org/Medication",
        "http://schema.org/Drug",
        "http://schema.org/MedicalEntity",
    ]


async def test_walk_ancestors_multi_parent_picks_first_lexicographically(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    path = await walk_ancestors(
        "http://schema.org/Hospital", graph_storage, max_depth=10
    )
    assert path[0] == "http://schema.org/Hospital"
    assert path[-1] == "http://schema.org/Thing"
    assert len(path) == 3
    # MedicalEntity < Organization lexically, so picked first.
    assert path[1] == "http://schema.org/MedicalEntity"


async def test_walk_ancestors_unknown_iri_returns_empty(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    assert await walk_ancestors("http://nope/X", graph_storage) == []
