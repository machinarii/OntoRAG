"""Tests for ontorag.taxonomy.classifier."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest_asyncio

from ontorag.kg.nano_vector_db_impl import NanoVectorDBStorage
from ontorag.kg.networkx_impl import NetworkXStorage
from ontorag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from ontorag.namespace import NameSpace
from ontorag.taxonomy.class_index import build_class_index
from ontorag.taxonomy.classifier import DocumentClassifier
from ontorag.taxonomy.constants import UNCATEGORIZED_IRI
from ontorag.taxonomy.graph_loader import load_taxonomy_to_graph
from ontorag.taxonomy.parser import parse_ntriples_file
from ontorag.utils import EmbeddingFunc

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"
_DIM = 16


async def _embed(texts: list[str], **_: object) -> np.ndarray:
    out = np.zeros((len(texts), _DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for j, ch in enumerate(t.lower()[:_DIM]):
            out[i, j] = ord(ch) / 255.0
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


def _make_llm(returns):
    calls: list[dict] = []

    async def llm(prompt: str, system_prompt: str | None = None, **kw):
        calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if isinstance(returns, Exception):
            raise returns
        return returns

    llm.calls = calls  # type: ignore[attr-defined]
    return llm


@pytest_asyncio.fixture
async def classifier_factory(tmp_path: Path):
    initialize_share_data()
    embed = EmbeddingFunc(embedding_dim=_DIM, max_token_size=8192, func=_embed)
    graph = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="clftest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=embed,
    )
    await graph.initialize()
    vdb = NanoVectorDBStorage(
        namespace=NameSpace.VECTOR_STORE_YAGO_CLASSES,
        workspace="clftest",
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
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    await build_class_index([c.iri for c in classes], graph, vdb)

    def _factory(llm_func, **overrides):
        kwargs = {
            "vector_storage": vdb,
            "llm_func": llm_func,
            "candidate_count": 10,
        }
        kwargs.update(overrides)
        return DocumentClassifier(**kwargs)

    yield _factory
    await vdb.finalize()
    await graph.finalize()
    finalize_share_data()


async def test_returns_uncategorized_when_no_candidate_scores_above_min(
    classifier_factory,
):
    llm = _make_llm(
        json.dumps(
            {
                "assignments": [
                    {"iri": "http://schema.org/Drug", "score": 0.1},
                    {"iri": "http://schema.org/Person", "score": 0.05},
                ]
            }
        )
    )
    result = await classifier_factory(llm).classify("some random text")
    assert len(result) == 1
    assert result[0]["iri"] == UNCATEGORIZED_IRI
    assert result[0]["score"] == 0.0


async def test_keeps_single_top_class_when_secondaries_below_ratio(classifier_factory):
    llm = _make_llm(
        json.dumps(
            {
                "assignments": [
                    {"iri": "http://schema.org/Drug", "score": 0.9},
                    {"iri": "http://schema.org/Person", "score": 0.2},
                ]
            }
        )
    )
    result = await classifier_factory(llm).classify("aspirin tablet")
    assert [r["iri"] for r in result] == ["http://schema.org/Drug"]


async def test_keeps_secondaries_above_ratio(classifier_factory):
    llm = _make_llm(
        json.dumps(
            {
                "assignments": [
                    {"iri": "http://schema.org/Drug", "score": 0.9},
                    {"iri": "http://schema.org/MedicalEntity", "score": 0.7},
                    {"iri": "http://schema.org/Person", "score": 0.2},
                ]
            }
        )
    )
    result = await classifier_factory(llm).classify("aspirin")
    iris = [r["iri"] for r in result]
    assert iris == [
        "http://schema.org/Drug",
        "http://schema.org/MedicalEntity",
    ]


async def test_caps_at_max_classes(classifier_factory):
    # All 5 IRIs must be in the candidate set so they survive the
    # in-vocab filter. With candidate_count=10 the fixture surfaces all
    # 8 fixture classes, so any subset works.
    real_iris = [
        "http://schema.org/Drug",
        "http://schema.org/Medication",
        "http://schema.org/MedicalEntity",
        "http://schema.org/Person",
        "http://schema.org/Organization",
    ]
    assignments = [
        {"iri": iri, "score": 0.9 - i * 0.01} for i, iri in enumerate(real_iris)
    ]
    llm = _make_llm(json.dumps({"assignments": assignments}))
    result = await classifier_factory(llm, max_classes=3).classify(
        "multi-topic doc covering many subjects"
    )
    assert len(result) == 3


async def test_uncategorized_when_llm_returns_malformed_json(classifier_factory):
    llm = _make_llm("not json at all")
    result = await classifier_factory(llm).classify("anything")
    assert result == [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]


async def test_uncategorized_when_llm_raises(classifier_factory):
    llm = _make_llm(RuntimeError("api boom"))
    result = await classifier_factory(llm).classify("anything")
    assert result == [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]


async def test_llm_prompt_contains_candidate_iris(classifier_factory):
    llm = _make_llm(json.dumps({"assignments": []}))
    inst = classifier_factory(llm)
    await inst.classify("drug medication")
    assert llm.calls, "LLM was not invoked"
    prompt = llm.calls[0]["prompt"]
    assert "http://schema.org/" in prompt


# ---------------------------------------------------------------------------
# paperless-ngx lessons: prompt discipline, free-form name reconciliation,
# neighbour candidates, supervised prior.
# ---------------------------------------------------------------------------


async def test_prompt_marks_document_untrusted_and_candidates_optional(
    classifier_factory,
):
    llm = _make_llm(
        json.dumps({"assignments": [{"iri": "http://schema.org/Drug", "score": 0.9}]})
    )
    await classifier_factory(llm).classify("aspirin")
    prompt = llm.calls[0]["prompt"]
    system = llm.calls[0]["system_prompt"]
    assert "options, not requirements" in prompt
    assert "untrusted" in prompt.lower() and "do not follow" in prompt.lower()
    assert "suggested_names" in system


async def test_suggested_names_are_reconciled_to_candidates(classifier_factory):
    llm = _make_llm(
        json.dumps(
            {
                "assignments": [{"iri": "http://schema.org/Drug", "score": 0.9}],
                "suggested_names": [
                    "Medical Entity",
                    "Drug",
                    "Space Elevator Engineering",
                ],
            }
        )
    )
    result = await classifier_factory(llm).classify_detailed("aspirin tablet")
    iris = [a["iri"] for a in result.assignments]
    assert iris[0] == "http://schema.org/Drug"
    # "Medical Entity" fuzzy-matches the candidate label "MedicalEntity" (one-to-one).
    assert "http://schema.org/MedicalEntity" in iris
    assert result.unmatched_names == ["Space Elevator Engineering"]
    assert result.llm_called is True
    # classify() keeps its contract.
    plain = await classifier_factory(
        _make_llm(
            json.dumps(
                {"assignments": [{"iri": "http://schema.org/Drug", "score": 0.9}]}
            )
        )
    ).classify("aspirin")
    assert plain == [{"iri": "http://schema.org/Drug", "score": 0.9}]


async def test_neighbor_provider_candidates_reach_the_prompt(classifier_factory):
    seen = {}

    async def provider(text):
        seen["text"] = text
        return [
            {
                "iri": "http://schema.org/Person",
                "label": "Person",
                "score": 1.0,
                "support": 4,
            }
        ]

    llm = _make_llm(
        json.dumps({"assignments": [{"iri": "http://schema.org/Person", "score": 0.8}]})
    )
    result = await classifier_factory(
        llm, neighbor_provider=provider
    ).classify_detailed("some text")
    assert seen["text"] == "some text"
    assert any(c["iri"] == "http://schema.org/Person" for c in result.candidates)
    assert "Person" in llm.calls[0]["prompt"]
    assert [a["iri"] for a in result.assignments] == ["http://schema.org/Person"]


async def test_confident_prior_skips_the_llm(classifier_factory):
    class _Prior:
        labels = {"http://schema.org/Drug": "Drug"}

        def predict_proba(self, text):
            return {"http://schema.org/Drug": 0.95, "http://schema.org/Person": 0.05}

    llm = _make_llm(RuntimeError("must not be called"))
    result = await classifier_factory(llm, prior=_Prior()).classify_detailed("aspirin")
    assert result.llm_called is False
    assert result.assignments[0]["iri"] == "http://schema.org/Drug"
    assert result.assignments[0]["score"] == 0.95
    assert llm.calls == []


async def test_unconfident_prior_only_reranks_candidates(classifier_factory):
    class _Prior:
        labels = {"http://schema.org/Person": "Person"}

        def predict_proba(self, text):
            return {"http://schema.org/Person": 0.6}

    llm = _make_llm(
        json.dumps({"assignments": [{"iri": "http://schema.org/Person", "score": 0.7}]})
    )
    result = await classifier_factory(llm, prior=_Prior()).classify_detailed("aspirin")
    assert result.llm_called is True
    person = next(
        c for c in result.candidates if c["iri"] == "http://schema.org/Person"
    )
    assert person["score"] >= 0.6
