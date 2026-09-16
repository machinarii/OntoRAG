import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from ontorag.base import QueryParam
from ontorag.retrieval.index import ContextualVectors, IndexedChunks, LexicalIndex
from ontorag.retrieval.runtime import (
    RetrievalRuntime,
    evidence_fingerprint,
    fuse_rankings,
    gather_strict,
    prepare_query,
    select_diverse,
    verified_model,
)


class KV:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.global_config = {}

    async def get_by_ids(self, ids):
        return [self.rows.get(key) for key in ids]

    async def get_by_id(self, key):
        return self.rows.get(key)

    async def upsert(self, rows):
        self.rows.update(rows)

    async def delete(self, ids):
        for key in ids:
            self.rows.pop(key, None)

    async def drop(self):
        self.rows.clear()
        return {"status": "success"}


@pytest.fixture
async def index(tmp_path):
    result = LexicalIndex(tmp_path / "retrieval.sqlite3")
    await result.initialize()
    return result


def runtime(chunks, index=None):
    return RetrievalRuntime(
        SimpleNamespace(
            text_chunks=KV(chunks),
            entity_chunks=KV(),
            relation_chunks=KV(),
            doc_status=KV(),
        ),
        index,
    )


async def test_index_exact_codes_and_safe_query(index):
    await index.upsert(
        {
            "a": {"content": "PX-200 maintenance every 90 days"},
            "b": {"content": "Other equipment replacement"},
        }
    )
    assert await index.search('PX-200 " OR NOT *', 10) == ["a"]
    assert await index.search('"*()[]', 10) == []
    await index.delete(["a"])
    assert await index.search("PX-200", 10) == []


async def test_index_updates_and_other_worker_sees_revision(index):
    other = LexicalIndex(index.path)
    await other.initialize()
    before = await other.revision()
    await index.upsert({"a": {"content": "old"}})
    await index.upsert({"a": {"content": "new"}})
    assert await other.revision() > before
    assert await other.search("old", 10) == []
    assert await other.search("new", 10) == ["a"]


async def test_chunk_mutations_are_mirrored(index):
    store = IndexedChunks(KV(), index)
    await store.upsert({"a": {"content": "maintenance"}})
    assert await index.search("maintenance", 2) == ["a"]
    await store.delete(["a"])
    assert await index.search("maintenance", 2) == []
    await store.upsert({"b": {"content": "maintenance"}})
    assert (await store.drop())["status"] == "success"
    assert await index.search("maintenance", 2) == []


async def test_mirror_failure_propagates(index):
    store = IndexedChunks(KV(), index)
    index.upsert = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        await store.upsert({"a": {"content": "new"}})
    assert await store.get_by_id("a") == {"content": "new"}


async def test_contextual_embedding_does_not_change_source():
    store = KV()
    store.embedding_func = SimpleNamespace(max_token_size=None)
    row = {
        "content": "Replace every 90 days",
        "file_path": "Pump PX-200",
        "heading": {"heading": "Maintenance", "parent_headings": ["Service"]},
    }
    await ContextualVectors(store).upsert({"a": row})
    assert "Pump PX-200" in store.rows["a"]["content"]
    assert "Maintenance" in store.rows["a"]["content"]
    assert row["content"] == "Replace every 90 days"


def test_rrf_rewards_agreement_without_comparing_scores():
    result = fuse_rankings(
        [{"chunk_id": "a", "score": 1000}, {"chunk_id": "b", "score": 0.1}],
        [{"chunk_id": "b", "score": -0.5}],
    )
    assert [row["chunk_id"] for row in result] == ["b", "a"]


async def test_candidates_hydrate_and_cache_invalidates(index):
    chunks = {"a": {"content": "PX-200 maintenance", "full_doc_id": "doc"}}
    rt = runtime(chunks, index)
    embed = AsyncMock(return_value=np.array([[1.0, 0.0]]))
    vdb = SimpleNamespace(
        embedding_func=embed,
        query=AsyncMock(
            return_value=[
                {"id": "gone", "content": "deleted"},
                {"id": "a", "content": "stale"},
            ]
        ),
    )
    await index.upsert(chunks)
    param = QueryParam(enable_lexical=True, cache_retrieval=True, retrieval_top_k=70)
    hits = await rt.candidates("PX-200", vdb, param)
    assert [row["content"] for row in hits] == ["PX-200 maintenance"]
    await rt.candidates("PX-200", vdb, param)
    assert vdb.query.await_count == 1
    assert vdb.query.call_args.kwargs["top_k"] == 70
    await index.delete(["a"])
    rt.rag.text_chunks.rows.clear()
    assert await rt.candidates("PX-200", vdb, param) == []
    assert vdb.query.await_count == 2
    assert embed.await_count == 1


async def test_missing_index_is_an_error():
    with pytest.raises(ValueError, match="ENABLE_LEXICAL_INDEX"):
        await runtime({}).candidates("test", None, QueryParam(enable_lexical=True))


async def test_authoritative_empty_attribution_never_falls_back():
    rt = runtime({})
    rt.rag.entity_chunks.rows = {
        "pump": {"chunk_ids": []},
        "valve": {"chunk_ids": ["new"]},
    }
    rows = await rt.attribution(
        [
            {"entity_name": "pump", "source_id": "stale"},
            {"entity_name": "valve", "source_id": "truncated"},
            {"entity_name": "legacy", "source_id": "fallback"},
        ]
    )
    assert [r["source_id"] for r in rows] == ["", "new", "fallback"]


async def test_attribution_errors_propagate():
    rt = runtime({})
    rt.rag.entity_chunks.get_by_ids = AsyncMock(side_effect=ConnectionError())
    with pytest.raises(ConnectionError):
        await rt.attribution([{"entity_name": "pump"}])


async def test_version_and_date_filters(index):
    rt = runtime({}, index)
    await index.set_document_metadata(
        "old",
        {
            "version": "1",
            "superseded": True,
            "effective_from": "2020-01-01",
            "effective_to": "2023-12-31",
        },
    )
    await index.set_document_metadata(
        "new", {"version": "2", "effective_from": "2024-01-01"}
    )
    rows = [{"full_doc_id": key} for key in ("old", "new", "unknown")]
    assert [
        r["full_doc_id"]
        for r in await rt.filter_chunks(rows, QueryParam(document_version="1"))
    ] == ["old"]
    assert [
        r["full_doc_id"]
        for r in await rt.filter_chunks(rows, QueryParam(as_of="2022-01-01"))
    ] == ["old"]
    assert [
        r["full_doc_id"]
        for r in await rt.filter_chunks(rows, QueryParam(exclude_superseded=True))
    ] == ["new", "unknown"]


async def test_neighbor_expansion_keeps_document_boundary():
    rt = runtime(
        {
            "a": {"content": "header", "full_doc_id": "doc"},
            "b": {"content": "body", "full_doc_id": "doc"},
            "c": {"content": "other", "full_doc_id": "other"},
        }
    )
    rt.rag.doc_status.rows = {"doc": {"chunks_list": ["a", "b", "c"]}}
    rows = await rt.expand(
        [{"chunk_id": "b", **rt.rag.text_chunks.rows["b"]}],
        QueryParam(context_neighbors=1),
    )
    assert {r["chunk_id"] for r in rows} == {"a", "b"}


def test_diversity_removes_duplicates_and_retains_comparison():
    rows = [
        {"chunk_id": "a", "content": "Pump A uses oil", "file_path": "a"},
        {"chunk_id": "b", "content": "Pump A uses oil", "file_path": "a"},
        {"chunk_id": "c", "content": "Pump B uses water", "file_path": "b"},
    ]
    assert {
        r["chunk_id"] for r in select_diverse(rows, "compare Pump A and Pump B")
    } == {"a", "c"}


async def test_followup_rewrite_preserves_request():
    llm = AsyncMock(return_value='{"query": "PX-200 maintenance interval"}')
    param = QueryParam(
        rewrite_followups=True,
        conversation_history=[{"role": "user", "content": "PX-200"}],
    )
    result = await prepare_query(
        "What about its maintenance?", param, {"role_llm_funcs": {"keyword": llm}}
    )
    assert result._retrieval_query == "PX-200 maintenance interval"
    assert not hasattr(param, "_retrieval_query")
    await prepare_query(
        "PX-200 maintenance interval", param, {"role_llm_funcs": {"keyword": llm}}
    )
    assert llm.await_count == 1


@pytest.mark.parametrize(
    "verdict,answer,supported",
    [
        ('{"supported":true,"conflicting":false}', "Every 90 days [1]", True),
        ('{"supported":true,"conflicting":false}', "Every 90 days [9]", False),
        ('{"supported":true,"conflicting":true}', "Every 90 days [1]", False),
        ("not JSON", "Every 90 days [1]", False),
    ],
)
async def test_verification_is_buffered_and_fails_closed(verdict, answer, supported):
    model = AsyncMock(side_effect=[answer, verdict])
    report = {}
    result = await verified_model(model, QueryParam(verify_answer=True), report)(
        "interval?", system_prompt="[1] Every 90 days", stream=True
    )
    assert report["supported"] is supported
    assert (result == answer) is supported
    assert all(call.kwargs["stream"] is False for call in model.call_args_list)


async def test_parallel_failure_joins_siblings():
    started, stopped = asyncio.Event(), asyncio.Event()

    async def slow():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def fail():
        await started.wait()
        raise ConnectionError("backend unavailable")

    with pytest.raises(ConnectionError):
        await gather_strict(slow(), fail())
    assert stopped.is_set()


def test_answer_cache_changes_with_evidence():
    assert evidence_fingerprint("old evidence", QueryParam()) != evidence_fingerprint(
        "new evidence", QueryParam()
    )


@pytest.mark.parametrize(
    "options",
    [
        {"retrieval_top_k": 0},
        {"rerank_top_k": 1001},
        {"context_neighbors": 4},
        {"as_of": "2026-02-30"},
        {"as_of": "20260101"},
        {"context_neighbors": True},
        {"document_version": ""},
    ],
)
def test_sdk_options_are_validated(options):
    with pytest.raises(ValueError):
        QueryParam(**options)


async def test_incomplete_index_refuses_search_until_rebuilt(index):
    await index.upsert({"a": {"content": "pump"}})
    await index.set_ready(False)
    with pytest.raises(RuntimeError, match="rebuild"):
        await index.search("pump", 10)
    await index.set_ready(True)
    assert await index.search("pump", 10) == ["a"]


async def test_rewrite_retains_original_candidates():
    rt = runtime({"a": {"content": "original"}, "b": {"content": "rewritten"}})
    vdb = SimpleNamespace(
        embedding_func=None, query=AsyncMock(side_effect=[[{"id": "b"}], [{"id": "a"}]])
    )
    param = QueryParam()
    param._original_query = "original"
    rows = await rt.candidates("rewritten", vdb, param)
    assert {r["chunk_id"] for r in rows} == {"a", "b"}
    assert vdb.query.call_args_list[1].args[0] == "original"


async def test_metadata_and_rebuild_reservations(index):
    from ontorag.base import CURSOR_END, DocStatusPage
    from ontorag.kg.shared_storage import (
        initialize_share_data,
        get_namespace_data,
        finalize_share_data,
    )
    from ontorag.retrieval.maintenance import rebuild_index, set_document_metadata

    initialize_share_data(1)
    try:
        rt = runtime({"a": {"content": "pump", "full_doc_id": "doc"}}, index)
        rag = rt.rag
        rag.workspace = "retrieval-maintenance-test"
        rag._retrieval_runtime = rt
        rag.doc_status.rows = {"doc": {"chunks_list": ["a"]}}
        rag.doc_status.get_docs_by_statuses_page = AsyncMock(
            return_value=DocStatusPage(
                docs={"doc": rag.doc_status.rows["doc"]}, next_position=CURSOR_END
            )
        )
        from ontorag.kg.shared_storage import initialize_pipeline_status

        await initialize_pipeline_status(workspace=rag.workspace)
        state = await get_namespace_data("pipeline_status", workspace=rag.workspace)
        state.update(busy=True)
        with pytest.raises(RuntimeError, match="busy"):
            await rebuild_index(rag)
        state.update(busy=False)
        await set_document_metadata(rag, "doc", {"version": "v2"})
        state["scan_deferred_processing"] = True
        rag.apipeline_process_enqueue_documents = AsyncMock(
            side_effect=lambda: state.update(scan_deferred_processing=False)
        )
        result = await rebuild_index(rag)
        rag.apipeline_process_enqueue_documents.assert_awaited_once()
        assert result["indexed_chunks"] == 1
        assert await index.document_metadata(["doc"]) == {"doc": {"version": "v2"}}
        assert not state["scanning"] and not state["scanning_exclusive"]
        rag.doc_status.get_docs_by_statuses_page.side_effect = ConnectionError(
            "offline"
        )
        with pytest.raises(ConnectionError):
            await rebuild_index(rag)
        assert not state["scanning"]
        with pytest.raises(RuntimeError, match="rebuild"):
            await index.search("pump", 10)
    finally:
        finalize_share_data()


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("mode", ["naive", "mix"])
async def test_real_sdk_storage_query_and_verification(tmp_path, retry, mode):
    from ontorag import OntoRAG
    from ontorag.utils import EmbeddingFunc
    from ontorag.kg.shared_storage import finalize_share_data

    async def embed(texts, **kwargs):
        return np.tile(np.array([1.0, 0.0, 0.0]), (len(texts), 1))

    calls = []

    async def model(query, **kwargs):
        calls.append(query)
        if kwargs.get("system_prompt", "").startswith("Audit"):
            if retry and len(calls) == 2:
                return '{"supported":false,"conflicting":false,"missing_evidence":"maintenance interval"}'
            return '{"supported":true,"conflicting":false}'
        return "Service every 90 days [1]."

    rag = OntoRAG(
        working_dir=str(tmp_path),
        workspace="sdk-retrieval",
        embedding_func=EmbeddingFunc(embedding_dim=3, max_token_size=1024, func=embed),
        llm_model_func=model,
        enable_lexical_index=True,
        enable_contextual_embeddings=True,
    )
    try:
        await rag.initialize_storages()
        rows = {
            "chunk-a": {
                "content": "PX-200: service every 90 days.",
                "file_path": "manual.md",
                "full_doc_id": "doc-a",
                "tokens": 12,
                "chunk_order_index": 0,
            }
        }
        await rag.text_chunks.upsert(rows)
        await rag.chunks_vdb.upsert(rows)
        await rag._insert_done()
        options = QueryParam(
            mode=mode,
            ll_keywords=["PX-200"],
            hl_keywords=["maintenance"],
            enable_lexical=True,
            enable_rerank=False,
            cache_retrieval=True,
        )
        data = await rag.aquery_data("PX-200 interval?", options)
        assert data["data"]["chunks"][0]["content"] == rows["chunk-a"]["content"]
        assert not calls
        options.verify_answer = True
        options.retry_missing_evidence = retry
        answer = await rag.aquery_llm("PX-200 interval?", options)
        assert answer["metadata"]["verification"]["supported"] is True
        assert answer["llm_response"]["content"] == "Service every 90 days [1]."
        assert len(calls) == (4 if retry else 2)
        if retry:
            assert answer["metadata"]["retrieval_retries"] == 1
        await rag.text_chunks.delete(["chunk-a"])
        data = await rag.aquery_data("PX-200 interval?", options)
        assert not data.get("data", {}).get("chunks")
    finally:
        await rag.finalize_storages()
        finalize_share_data()


async def test_verifier_cannot_cite_template_examples():
    model = AsyncMock(
        side_effect=["Claim [9]", '{"supported":true,"conflicting":false}']
    )
    report = {}
    answer = await verified_model(
        model, QueryParam(verify_answer=True), report, reference_ids=["1"]
    )("question", system_prompt="Reference [1] source; formatting example [9]")
    assert report["supported"] is False
    assert answer.startswith("The retrieved evidence")
