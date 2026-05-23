"""Load parsed YAGO classes into a BaseGraphStorage instance.

Classes become nodes; rdfs:subClassOf statements become edges with a
distinct relation_type so we can filter taxonomy edges away from any
domain edges that might share the storage backend in the future. Each
subClassOf edge carries the child IRI in its data so ancestor walking
can distinguish "walk toward parents" from "walk toward children" in
the underlying undirected graph.

Ancestor walking is done client-side via repeated get_edge calls rather
than relying on backend-specific traversal — keeps the code identical
across NetworkX, Neo4j, Memgraph, etc.
"""

from __future__ import annotations

import time
from typing import Iterable

from lightrag.base import BaseGraphStorage
from lightrag.taxonomy.constants import YAGO_NODE_ENTITY_TYPE
from lightrag.taxonomy.parser import YagoClass

SUBCLASS_OF_EDGE_TYPE = "subClassOf"


async def load_taxonomy_to_graph(
    classes: Iterable[YagoClass],
    graph_storage: BaseGraphStorage,
) -> None:
    """Upsert every YagoClass and its subClassOf edges into `graph_storage`.

    Idempotent: re-running with the same classes overwrites existing nodes
    and edges with identical content. Calls `index_done_callback` once at
    the end so file-backed stores persist a single time.
    """
    now = int(time.time())
    classes = list(classes)

    for cls in classes:
        await graph_storage.upsert_node(
            cls.iri,
            {
                "entity_id": cls.iri,
                "entity_type": YAGO_NODE_ENTITY_TYPE,
                "label": cls.label,
                "description": cls.comment,
                "source_id": "yago-taxonomy",
                "file_path": "yago-taxonomy",
                "created_at": now,
            },
        )

    for cls in classes:
        for parent in cls.parent_iris:
            await graph_storage.upsert_edge(
                cls.iri,
                parent,
                {
                    "relation_type": SUBCLASS_OF_EDGE_TYPE,
                    "child_iri": cls.iri,
                    "description": f"{cls.label} is a subclass of",
                    "keywords": "subClassOf,taxonomy",
                    "weight": 1.0,
                    "source_id": "yago-taxonomy",
                    "file_path": "yago-taxonomy",
                    "created_at": now,
                },
            )

    await graph_storage.index_done_callback()


async def walk_ancestors(
    iri: str,
    graph_storage: BaseGraphStorage,
    max_depth: int = 10,
) -> list[str]:
    """Return the IRI path from `iri` toward a root, capped at `max_depth`.

    The path always starts with `iri` itself when the node exists. With
    multiple parents we deterministically follow the lexicographically
    smallest one so render output is stable across runs.

    Filters edges by `child_iri == current` so an undirected backend
    doesn't let us drift sideways into a sibling.

    Returns [] if `iri` isn't in the graph.
    """
    node = await graph_storage.get_node(iri)
    if node is None:
        return []

    path: list[str] = [iri]
    current = iri
    visited: set[str] = {iri}

    while len(path) < max_depth:
        edges = await graph_storage.get_node_edges(current) or []
        parents: list[str] = []
        for src, tgt in edges:
            other = tgt if src == current else src
            if other in visited:
                continue
            edge = await graph_storage.get_edge(current, other)
            if edge is None:
                continue
            if edge.get("relation_type") != SUBCLASS_OF_EDGE_TYPE:
                continue
            if edge.get("child_iri") != current:
                continue
            parents.append(other)
        if not parents:
            break
        next_parent = sorted(parents)[0]
        path.append(next_parent)
        visited.add(next_parent)
        current = next_parent

    return path
