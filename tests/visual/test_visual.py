import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from ontorag.base import QueryParam
from ontorag.retrieval.runtime import RetrievalRuntime, fuse_rankings
from ontorag.visual.index import VisualIndex, normalized
from ontorag.visual.protocol import decode_image, validate_references
from ontorag.visual.runtime import VisualChunks, VisualRuntime, read_asset
from ontorag.visual.worker import open_image, crop_reference

pytestmark = pytest.mark.offline


def picture(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (20, 10), color).save(stream, format="PNG")
    return stream.getvalue()


def reference(**kwargs):
    return {"image": base64.b64encode(picture()).decode(), **kwargs}


class KV:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.global_config = {}

    async def upsert(self, rows):
        self.rows.update(rows)

    async def get_by_id(self, key):
        return self.rows.get(key)

    async def get_by_ids(self, keys):
        return [self.rows.get(key) for key in keys]

    async def delete(self, keys):
        for key in keys:
            self.rows.pop(key, None)

    async def drop(self):
        self.rows.clear()
        return {"status": "success"}


@pytest.fixture
async def index(tmp_path):
    index = VisualIndex(tmp_path / "visual.sqlite3")
    await index.initialize()
    return index


@pytest.fixture
async def runtime(tmp_path, index):
    root = tmp_path / "document.parsed"
    root.mkdir()
    (root / "doc.blocks.jsonl").write_text("{}\n")
    (root / "figure.png").write_bytes(picture())
    (root / "doc.drawings.json").write_text(
        json.dumps({"drawings": {"im-1": {"path": "figure.png"}}})
    )
    row = {
        "full_doc_id": "doc",
        "content": "Red pump diagram. Service annually.",
        "file_path": "manual.md",
        "sidecar": {
            "type": "drawing",
            "id": "im-1",
            "refs": [{"type": "drawing", "id": "im-1"}],
        },
    }
    rag = SimpleNamespace(
        full_docs=KV({"doc": {"sidecar_location": root.as_uri() + "/"}}),
        text_chunks=KV(),
        doc_status=KV(),
        entity_chunks=KV(),
        relation_chunks=KV(),
    )
    client = SimpleNamespace(
        embed=AsyncMock(return_value=("siglip-pinned", [[1.0, 0.0, 0.0]])),
        rerank=AsyncMock(return_value=[3.0]),
    )
    runtime = VisualRuntime(rag, index, client)
    rag._retrieval_runtime = RetrievalRuntime(rag)
    rag._visual_runtime = runtime
    rag.text_chunks = VisualChunks(rag.text_chunks, runtime)
    await rag.text_chunks.upsert({"chunk": row})
    return runtime


def test_image_protocol_and_normalized_box():
    assert decode_image(reference()["image"]) == picture()
    assert open_image(reference()["image"]).size == (20, 10)
    assert crop_reference(reference(box=[0.25, 0.2, 0.75, 0.8])).size == (10, 6)
    validate_references([reference(box=[0, 0, 1, 1])])


@pytest.mark.parametrize(
    "refs",
    [
        [],
        [reference()] * 5,
        [{"image": "http://example/image.png"}],
        [reference(box=[0, 0, 0, 1])],
        [reference(box=[0, 0, 2, 1])],
        [reference(box=[0, 0, float("nan"), 1])],
        [reference(path="/private")],
    ],
)
def test_bad_references_are_rejected(refs):
    with pytest.raises(ValueError):
        validate_references(refs)


@pytest.mark.parametrize(
    "vectors", [[[0, 0]], [[1, float("nan")]], [[1, float("inf")]], [1, 2]]
)
def test_invalid_vectors_are_rejected(vectors):
    with pytest.raises(ValueError):
        normalized(vectors)


async def test_exact_search_and_model_identity(index):
    assets = [
        dict(id="a", doc_id="d", drawing_id="a", digest="sha-a"),
        dict(id="b", doc_id="d", drawing_id="b", digest="sha-b"),
    ]
    await index.replace_chunks(
        ["c1", "c2"], assets, [("c1", "a"), ("c2", "b")], "space", [[1, 0], [0, 1]]
    )
    rows = await index.search_vectors([0, 3], "space", 2)
    assert [r["id"] for r in rows] == ["b", "a"]
    assert rows[0]["visual_score"] == 1.0
    with pytest.raises(ValueError, match="model changed"):
        await index.search_vectors([0, 1], "other-space", 2)
    with pytest.raises(ValueError, match="model changed"):
        await index.search_vectors([0, 1, 2], "space", 2)
    await index.set_ready(False)
    with pytest.raises(RuntimeError, match="rebuild"):
        await index.search_vectors([0, 1], "space", 2)


async def test_shared_figure_survives_until_last_chunk_deleted(index):
    assets = [dict(id="a", doc_id="d", drawing_id="im", digest="sha")]
    await index.replace_chunks(
        ["c1", "c2"], assets, [("c1", "a"), ("c2", "a")], "space", [[1, 0]]
    )
    await index.delete(["c1"])
    assert (await index.search_vectors([1, 0], "space", 2))[0]["chunk_ids"] == ["c2"]
    await index.delete(["c2"])
    assert await index.search_vectors([1, 0], "space", 2) == []


async def test_visual_retrieval_reranks_and_preserves_source(runtime):
    rows = await runtime.search(
        "pump", QueryParam(enable_visual=True, visual_references=[reference()])
    )
    assert rows[0]["content"] == "Red pump diagram. Service annually."
    assert rows[0]["visual_matches"] == [
        {"drawing_id": "im-1", "visual_score": 1.0, "foundyou_score": 3.0}
    ]
    assert not any(key in rows[0] for key in ("image", "root", "digest"))
    assert runtime.client.rerank.await_count == 1
    await runtime.rag.text_chunks.delete(["chunk"])
    assert await runtime.search("pump", QueryParam(enable_visual=True)) == []


async def test_text_visual_search_never_calls_foundyou(runtime):
    rows = await runtime.search("pump", QueryParam(enable_visual=True))
    assert rows
    runtime.client.rerank.assert_not_awaited()


async def test_deleted_during_inference_is_not_returned(runtime):
    async def delete_during_rerank(*args):
        await runtime.rag.text_chunks.delete(["chunk"])
        return [1.0]

    runtime.client.rerank.side_effect = delete_during_rerank
    assert (
        await runtime.search(
            "pump", QueryParam(enable_visual=True, visual_references=[reference()])
        )
        == []
    )


async def test_version_filter_runs_before_sending_candidate_pixels(runtime):
    rows = await runtime.search(
        "pump",
        QueryParam(
            enable_visual=True, visual_references=[reference()], document_version="v2"
        ),
    )
    assert rows == []
    runtime.client.rerank.assert_not_awaited()


async def test_stale_asset_is_not_silently_scored(runtime):
    root, _ = await runtime.manifest("doc")
    (root / "figure.png").write_bytes(picture("blue"))
    with pytest.raises(ValueError, match="changed"):
        await runtime.search("pump", QueryParam(enable_visual=True))


async def test_failed_write_requires_rebuild(runtime):
    runtime.client.embed.side_effect = ConnectionError("worker unavailable")
    with pytest.raises(ConnectionError):
        await runtime.rag.text_chunks.upsert(
            {"chunk": runtime.rag.text_chunks.rows["chunk"]}
        )
    with pytest.raises(RuntimeError, match="rebuild"):
        await runtime.index.search_vectors([1, 0, 0], "siglip-pinned", 3)
    await runtime.rag.text_chunks.drop()
    assert await runtime.index.search_vectors([1, 0, 0], "siglip-pinned", 3) == []


def test_path_traversal_and_symlinks_are_rejected(tmp_path):
    root = tmp_path / "sidecar"
    root.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(picture())
    (root / "link.png").symlink_to(outside)
    for value in ("../secret.png", str(outside), "link.png"):
        with pytest.raises(ValueError, match="within"):
            read_asset(root, {"path": value})


def test_fusion_keeps_visual_provenance_when_text_hit_precedes_it():
    rows = fuse_rankings(
        [{"chunk_id": "a", "source_type": "vector"}],
        [
            {
                "chunk_id": "a",
                "source_type": "visual",
                "visual_matches": [{"drawing_id": "im"}],
            }
        ],
    )
    assert rows[0]["visual_matches"] == [{"drawing_id": "im"}]
    assert rows[0]["retrieval_sources"] == ["vector", "visual"]


@pytest.mark.parametrize(
    "options",
    [
        {"visual_top_k": 101},
        {"visual_top_k": True},
        {"visual_references": [reference()]},
        {"mode": "bypass", "enable_visual": True},
    ],
)
def test_invalid_sdk_visual_options(options):
    with pytest.raises(ValueError):
        QueryParam(**options)


@pytest.mark.parametrize("mode", ["naive", "mix", "local"])
async def test_real_sdk_visual_lifecycle_and_rag(tmp_path, monkeypatch, mode):
    from ontorag import OntoRAG
    from ontorag.utils import EmbeddingFunc
    from ontorag.kg.shared_storage import finalize_share_data
    import numpy as np

    async def text_embed(texts, **kwargs):
        return np.tile(np.array([1.0, 0.0, 0.0]), (len(texts), 1))

    async def llm(*args, **kwargs):
        return "Service annually [1]."

    client = SimpleNamespace(
        embed=AsyncMock(return_value=("siglip-pinned", [[1.0, 0.0, 0.0]])),
        rerank=AsyncMock(return_value=[3.0]),
    )
    monkeypatch.setattr("ontorag.visual.client.VisualClient", lambda: client)
    root = tmp_path / "parsed"
    root.mkdir()
    (root / "doc.blocks.jsonl").write_text("{}\n")
    (root / "figure.png").write_bytes(picture())
    (root / "doc.drawings.json").write_text(
        json.dumps({"drawings": {"im": {"path": "figure.png"}}})
    )
    rag = OntoRAG(
        working_dir=str(tmp_path / "storage"),
        workspace="visual-sdk",
        enable_visual_search=True,
        embedding_func=EmbeddingFunc(
            embedding_dim=3, max_token_size=1024, func=text_embed
        ),
        llm_model_func=llm,
    )
    try:
        await rag.initialize_storages()
        await rag.full_docs.upsert(
            {
                "doc": {
                    "content": "Pump diagram",
                    "sidecar_location": root.as_uri() + "/",
                }
            }
        )
        await rag.doc_status.upsert(
            {
                "doc": {
                    "status": "processed",
                    "content_summary": "Pump diagram",
                    "content_length": 12,
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00",
                    "file_path": "manual.md",
                    "chunks_list": ["c"],
                    "chunks_count": 1,
                }
            }
        )
        rows = {
            "c": {
                "content": "Pump diagram: service annually.",
                "file_path": "manual.md",
                "full_doc_id": "doc",
                "tokens": 8,
                "chunk_order_index": 0,
                "sidecar": {
                    "type": "drawing",
                    "id": "im",
                    "refs": [{"type": "drawing", "id": "im"}],
                },
            }
        }
        await rag.text_chunks.upsert(rows)
        await rag.chunks_vdb.upsert(rows)
        await rag._insert_done()
        param = QueryParam(
            mode=mode,
            enable_visual=True,
            visual_references=[reference()],
            enable_rerank=False,
            ll_keywords=["pump"],
            hl_keywords=["maintenance"],
        )
        data = await rag.aquery_data("Find this pump", param)
        assert data["data"]["chunks"][0]["visual_matches"][0]["foundyou_score"] == 3.0
        result = await rag.aquery_llm("Find this pump", param)
        assert result["llm_response"]["content"] == "Service annually [1]."
        result = await rag.arebuild_visual_index()
        assert result["indexed_figure_chunks"] == 1
        assert (await rag.avisual_search("", param))[0]["chunk_id"] == "c"
        await rag.text_chunks.delete(["c"])
        assert await rag.avisual_search("", param) == []
    finally:
        await rag.finalize_storages()
        finalize_share_data()
