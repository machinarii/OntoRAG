"""Pick the ~200-class working vocabulary the classifier offers as choices.

Rationale (see docs/GraphAndRagArchitecture.md §5.2): the full YAGO 4.5
taxonomy has ~10K classes, many of them too fine-grained for an LLM to
choose stably (e.g. SerialKiller, Counterterrorism). Restricting the
classifier to a broad-but-bounded set of high-utility classes makes
ingestion-time classification reliable and keeps the prompt small.

Selection rule: rank classes by transitive descendant count (a proxy
for breadth) and take the top N, with manual exclusions for the root
and any other classes we don't want surfaced.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable

from lightrag.base import BaseGraphStorage
from lightrag.taxonomy.constants import DEFAULT_WORKING_VOCABULARY_SIZE
from lightrag.taxonomy.graph_loader import SUBCLASS_OF_EDGE_TYPE

_DEFAULT_EXCLUDED = frozenset({"http://schema.org/Thing"})


async def count_descendants(
    graph_storage: BaseGraphStorage,
    iris: Iterable[str],
) -> dict[str, int]:
    """Return {iri: transitive_descendant_count} for every iri in `iris`.

    Walks the inverse of subClassOf edges (parent → children) via BFS.
    O(N * E) in the worst case; fine for ~10K classes.
    """
    iris = list(iris)
    iri_set = set(iris)

    children_of: dict[str, list[str]] = {iri: [] for iri in iris}
    for iri in iris:
        edges = await graph_storage.get_node_edges(iri) or []
        for src, tgt in edges:
            other = tgt if src == iri else src
            edge = await graph_storage.get_edge(iri, other)
            if edge is None:
                continue
            if edge.get("relation_type") != SUBCLASS_OF_EDGE_TYPE:
                continue
            if edge.get("child_iri") != iri:
                continue
            # iri is the child on this edge → `other` is iri's parent.
            # Record iri as a direct child of `other` (inverse direction).
            if other in iri_set:
                children_of[other].append(iri)

    counts: dict[str, int] = {}
    for iri in iris:
        seen: set[str] = set()
        queue: deque[str] = deque(children_of[iri])
        while queue:
            child = queue.popleft()
            if child in seen:
                continue
            seen.add(child)
            queue.extend(children_of.get(child, []))
        counts[iri] = len(seen)
    return counts


async def select_working_vocabulary(
    graph_storage: BaseGraphStorage,
    iris: Iterable[str],
    target_size: int = DEFAULT_WORKING_VOCABULARY_SIZE,
    excluded_iris: Iterable[str] | None = None,
) -> list[str]:
    """Return up to `target_size` IRIs ordered by descendant count desc.

    Ties break lexicographically so the output is stable across runs.
    """
    excluded = set(_DEFAULT_EXCLUDED) | set(excluded_iris or ())
    candidates = [i for i in iris if i not in excluded]
    counts = await count_descendants(graph_storage, candidates)
    candidates.sort(key=lambda i: (-counts.get(i, 0), i))
    return candidates[:target_size]
