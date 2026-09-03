"""Tests for ontorag.taxonomy.neighbors — neighbour-label class candidates.

Design borrowed from paperless-ngx's ``build_taxonomy_candidates``: retrieve
the most similar already-classified documents and vote their classes,
weighted by similarity, as candidates for the new document.
"""

from __future__ import annotations

import pytest

from ontorag.taxonomy.constants import UNCATEGORIZED_IRI
from ontorag.taxonomy.neighbors import (
    merge_candidates,
    neighbor_class_candidates,
)

pytestmark = pytest.mark.offline


class _FakeDocVdb:
    def __init__(self, hits):
        self.hits = hits
        self.queries = []

    async def query(self, query_text, top_k, **kw):
        self.queries.append((query_text, top_k))
        return self.hits[:top_k]


def _classes_of(table):
    async def _lookup(doc_id):
        return table.get(doc_id, [])

    return _lookup


DRUG = "http://schema.org/Drug"
PERSON = "http://schema.org/Person"
MED = "http://schema.org/MedicalEntity"


async def test_votes_are_weighted_by_similarity_and_normalised():
    vdb = _FakeDocVdb(
        [
            {"full_doc_id": "d1", "distance": 0.1},  # sim 0.9
            {"full_doc_id": "d2", "distance": 0.5},  # sim 0.5
        ]
    )
    classes = {
        "d1": [{"iri": DRUG, "label": "Drug", "score": 1.0}],
        "d2": [
            {"iri": DRUG, "label": "Drug", "score": 1.0},
            {"iri": PERSON, "label": "Person", "score": 0.6},
        ],
    }
    out = await neighbor_class_candidates(
        "aspirin", document_vdb=vdb, classes_of=_classes_of(classes), top_k=5
    )
    assert [c["iri"] for c in out] == [DRUG, PERSON]
    assert out[0]["score"] == 1.0  # top vote normalised to 1
    assert out[0]["support"] == 2 and out[1]["support"] == 1
    assert 0 < out[1]["score"] < out[0]["score"]
    assert vdb.queries == [("aspirin", 5)]


async def test_multiple_chunks_of_one_document_count_once():
    vdb = _FakeDocVdb(
        [
            {"full_doc_id": "d1", "distance": 0.2},
            {"full_doc_id": "d1", "distance": 0.6},  # same doc, weaker chunk
            {"full_doc_id": "d2", "distance": 0.3},
        ]
    )
    classes = {
        "d1": [{"iri": DRUG, "label": "Drug"}],
        "d2": [{"iri": PERSON, "label": "Person"}],
    }
    out = await neighbor_class_candidates(
        "x", document_vdb=vdb, classes_of=_classes_of(classes)
    )
    by_iri = {c["iri"]: c for c in out}
    assert by_iri[DRUG]["support"] == 1
    # d1's best similarity (0.8) beats d2 (0.7): Drug ranks first.
    assert [c["iri"] for c in out] == [DRUG, PERSON]


async def test_uncategorized_and_unclassified_neighbours_are_ignored():
    vdb = _FakeDocVdb(
        [{"full_doc_id": "d1", "distance": 0.1}, {"full_doc_id": "d2", "distance": 0.1}]
    )
    classes = {
        "d1": [{"iri": UNCATEGORIZED_IRI, "label": "Uncategorized"}]
    }  # d2 unknown
    assert (
        await neighbor_class_candidates(
            "x", document_vdb=vdb, classes_of=_classes_of(classes)
        )
        == []
    )


async def test_lookup_failures_degrade_to_no_candidates():
    vdb = _FakeDocVdb([{"full_doc_id": "d1", "distance": 0.1}])

    async def boom(doc_id):
        raise RuntimeError("storage down")

    assert await neighbor_class_candidates("x", document_vdb=vdb, classes_of=boom) == []


def test_merge_keeps_best_score_per_iri_and_labels():
    index = [
        {"iri": DRUG, "label": "Drug", "score": 0.4},
        {"iri": MED, "label": "Medical entity", "score": 0.3},
    ]
    neigh = [
        {"iri": DRUG, "label": "", "score": 1.0, "support": 3},
        {"iri": PERSON, "label": "Person", "score": 0.5, "support": 1},
    ]
    merged = merge_candidates(index, neigh, neighbor_weight=0.5)
    by_iri = {c["iri"]: c for c in merged}
    assert by_iri[DRUG]["score"] == 0.5 and by_iri[DRUG]["label"] == "Drug"
    assert by_iri[PERSON]["score"] == 0.25 and by_iri[PERSON]["support"] == 1
    assert by_iri[MED]["score"] == 0.3
    assert [c["iri"] for c in merged] == [DRUG, MED, PERSON]
