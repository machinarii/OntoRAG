"""Neighbour-label class candidates.

Design borrowed from paperless-ngx's ``build_taxonomy_candidates`` (GPL code
not copied): instead of asking "which class labels look like this text", ask
"which classes did the most similar *already-classified documents* get".
Those votes encode prior human/LLM decisions and improve as the corpus grows;
``merge_candidates`` unions them with the class-index candidates so the LLM
sees both. Plan B supplies ``classes_of(doc_id)`` from ``doc_status.metadata``.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ontorag.base import BaseVectorStorage
from ontorag.taxonomy.constants import DEFAULT_NEIGHBOR_TOP_K, UNCATEGORIZED_IRI

logger = logging.getLogger(__name__)

ClassesOf = Callable[[str], Awaitable[list[dict[str, Any]]]]


def _similarity(hit: dict[str, Any]) -> float:
    distance = float(hit.get("distance", 1.0))
    return max(0.0, min(1.0, 1.0 - distance))


async def neighbor_class_candidates(
    query_text: str,
    *,
    document_vdb: BaseVectorStorage,
    classes_of: ClassesOf,
    top_k: int = DEFAULT_NEIGHBOR_TOP_K,
    doc_id_field: str = "full_doc_id",
) -> list[dict[str, Any]]:
    """Classes of the ``top_k`` most similar documents, voted by similarity.

    Returns ``[{"iri", "label", "score", "support"}, ...]`` sorted by score,
    with the best vote normalised to 1.0 and ``support`` = number of distinct
    neighbour documents that carry the class. Multiple chunks of one document
    count once (its best similarity). ``Uncategorized`` and unclassified
    neighbours contribute nothing. Any retrieval or lookup failure degrades
    to an empty list — candidates are a hint, never a hard dependency.
    """
    try:
        hits = await document_vdb.query(query_text, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("neighbor candidates: retrieval failed: %s", exc)
        return []

    weights: dict[str, float] = {}
    for hit in hits:
        doc_id = hit.get(doc_id_field) or hit.get("doc_id") or hit.get("id")
        if not isinstance(doc_id, str) or not doc_id:
            continue
        weights[doc_id] = max(weights.get(doc_id, 0.0), _similarity(hit))

    scores: dict[str, float] = {}
    support: dict[str, int] = {}
    labels: dict[str, str] = {}
    for doc_id, weight in weights.items():
        try:
            assignments = await classes_of(doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "neighbor candidates: classes_of(%s) failed: %s", doc_id, exc
            )
            return []
        for a in assignments or []:
            iri = a.get("iri") if isinstance(a, dict) else None
            if not isinstance(iri, str) or not iri or iri == UNCATEGORIZED_IRI:
                continue
            confidence = a.get("score", 1.0)
            confidence = (
                float(confidence) if isinstance(confidence, (int, float)) else 1.0
            )
            scores[iri] = scores.get(iri, 0.0) + weight * max(0.0, min(1.0, confidence))
            support[iri] = support.get(iri, 0) + 1
            if a.get("label") and not labels.get(iri):
                labels[iri] = str(a["label"])

    if not scores:
        return []
    top = max(scores.values()) or 1.0
    out = [
        {
            "iri": iri,
            "label": labels.get(iri, ""),
            "score": s / top,
            "support": support[iri],
        }
        for iri, s in scores.items()
    ]
    out.sort(key=lambda c: (-c["score"], -c["support"], c["iri"]))
    return out


def merge_candidates(
    index_candidates: list[dict[str, Any]],
    neighbor_candidates: list[dict[str, Any]],
    *,
    neighbor_weight: float = 1.0,
) -> list[dict[str, Any]]:
    """Union by IRI, keeping the best score (neighbour votes scaled by
    ``neighbor_weight``) and a non-empty label from either source."""
    merged: dict[str, dict[str, Any]] = {}
    for c in index_candidates:
        merged[c["iri"]] = {
            "iri": c["iri"],
            "label": c.get("label", ""),
            "score": float(c.get("score", 0.0)),
        }
    for c in neighbor_candidates:
        scaled = float(c.get("score", 0.0)) * neighbor_weight
        entry = merged.setdefault(
            c["iri"], {"iri": c["iri"], "label": "", "score": 0.0}
        )
        entry["score"] = max(entry["score"], scaled)
        if not entry.get("label") and c.get("label"):
            entry["label"] = c["label"]
        if "support" in c:
            entry["support"] = c["support"]
    out = list(merged.values())
    out.sort(key=lambda c: (-c["score"], c["iri"]))
    return out
