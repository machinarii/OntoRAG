"""Build and query the vector index of YAGO working-vocabulary classes.

Indexed text per class is `label — comment` (or just the label when no
comment exists). The same embedding model used by the rest of LightRAG
must be used here; switching embedding models after the index is built
requires rebuilding it from scratch.
"""

from __future__ import annotations

from typing import Any

from lightrag.base import BaseGraphStorage, BaseVectorStorage


def _iri_to_id(iri: str) -> str:
    return iri


def _compose_indexable_text(label: str, comment: str) -> str:
    label = label.strip()
    comment = comment.strip()
    if comment:
        return f"{label} — {comment}"
    return label


async def build_class_index(
    iris: list[str],
    graph_storage: BaseGraphStorage,
    vector_storage: BaseVectorStorage,
) -> None:
    """Embed every class in `iris` into `vector_storage`.

    Reads label + comment from `graph_storage`. Classes missing from the
    graph are silently skipped — caller is expected to have run
    load_taxonomy_to_graph first.

    Idempotent: re-running upserts the same IDs, overwriting prior vectors
    (useful when the working vocabulary changes).
    """
    payload: dict[str, dict[str, Any]] = {}
    for iri in iris:
        node = await graph_storage.get_node(iri)
        if node is None:
            continue
        label = node.get("label") or ""
        comment = node.get("description") or ""
        text = _compose_indexable_text(label, comment)
        if not text:
            continue
        payload[_iri_to_id(iri)] = {
            "iri": iri,
            "label": label,
            "content": text,
        }
    if payload:
        await vector_storage.upsert(payload)
        await vector_storage.index_done_callback()


async def retrieve_candidate_classes(
    query_text: str,
    vector_storage: BaseVectorStorage,
    top_n: int,
) -> list[dict[str, Any]]:
    """Return up to `top_n` candidate classes ranked by similarity.

    Each result is `{"iri": ..., "label": ..., "score": float}` where
    `score` is in [0, 1] (1 == perfect match). The underlying vector store
    returns a `distance` field (cosine distance); we convert to a
    similarity by `1 - distance`, clipped to [0, 1].
    """
    hits = await vector_storage.query(query_text, top_k=top_n)
    out: list[dict[str, Any]] = []
    for h in hits:
        distance = float(h.get("distance", 1.0))
        score = max(0.0, min(1.0, 1.0 - distance))
        out.append({
            "iri": h.get("iri") or h.get("id"),
            "label": h.get("label", ""),
            "score": score,
        })
    return out
