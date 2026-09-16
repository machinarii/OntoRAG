"""Retrieval controls survive HTTP validation and maintenance failures are explicit."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

with patch("sys.argv", ["ontorag-server"]):
    from ontorag.api.routers.query_routes import QueryRequest
    from ontorag.api.routers import retrieval_routes


@pytest.mark.parametrize(
    "options",
    [{"as_of": "2026-02-30"}, {"context_neighbors": 4}, {"retrieval_top_k": 0}],
)
def test_query_rejects_invalid_retrieval_options(options):
    with pytest.raises(ValidationError):
        QueryRequest(query="pump maintenance", **options)


def test_query_options_reach_sdk():
    request = QueryRequest(
        query="pump maintenance",
        enable_lexical=True,
        context_neighbors=2,
        document_version="v2",
        verify_answer=True,
        retry_missing_evidence=True,
    )
    param = request.to_query_params(False)
    assert param.enable_lexical and param.verify_answer and param.retry_missing_evidence
    assert param.context_neighbors == 2 and param.document_version == "v2"


async def test_maintenance_responses(monkeypatch):
    monkeypatch.setattr(
        retrieval_routes, "get_combined_auth_dependency", lambda key: lambda: None
    )
    rebuild = AsyncMock(return_value={"indexed_chunks": 3, "revision": 2})
    metadata = AsyncMock(return_value={"version": "v2"})
    monkeypatch.setattr(retrieval_routes, "rebuild_index", rebuild)
    monkeypatch.setattr(retrieval_routes, "set_document_metadata", metadata)
    app = FastAPI()
    app.include_router(retrieval_routes.create_retrieval_routes(SimpleNamespace()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/retrieval/rebuild")).json()["indexed_chunks"] == 3
        rebuild.side_effect = RuntimeError("Pipeline busy")
        assert (await client.post("/retrieval/rebuild")).status_code == 409
        result = await client.put(
            "/retrieval/documents/doc/metadata",
            json={"version": "v2", "effective_from": "2026-01-01"},
        )
        assert result.status_code == 200
        assert metadata.call_args.args[2]["effective_from"] == "2026-01-01"
        metadata.side_effect = KeyError("missing")
        assert (
            await client.put("/retrieval/documents/missing/metadata", json={})
        ).status_code == 404
        assert (
            await client.put(
                "/retrieval/documents/doc/metadata", json={"unknown": True}
            )
        ).status_code == 422
