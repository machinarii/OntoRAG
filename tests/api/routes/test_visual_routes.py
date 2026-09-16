from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

with patch("sys.argv", ["ontorag-server"]):
    from ontorag.api.routers import visual_routes
    from ontorag.api.routers.query_routes import QueryRequest


def test_visual_options_reach_query_sdk():
    request = QueryRequest(
        query="pump diagram", enable_visual=True, visual_top_k=15, visual_rerank=False
    )
    param = request.to_query_params(False)
    assert param.enable_visual and param.visual_top_k == 15 and not param.visual_rerank


async def test_visual_search_and_rebuild(monkeypatch):
    monkeypatch.setattr(
        visual_routes, "get_combined_auth_dependency", lambda key: lambda: None
    )
    rag = SimpleNamespace(
        _visual_runtime=True,
        avisual_search=AsyncMock(
            return_value=[{"chunk_id": "c", "visual_matches": [{"drawing_id": "im"}]}]
        ),
        arebuild_visual_index=AsyncMock(return_value={"indexed_figure_chunks": 2}),
    )
    app = FastAPI()
    app.include_router(visual_routes.create_visual_routes(rag))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/visual/search", json={"query": "pump", "top_k": 7})
        assert result.status_code == 200
        assert result.json()["chunks"][0]["visual_matches"] == [{"drawing_id": "im"}]
        assert rag.avisual_search.call_args.args[1].visual_top_k == 7
        for body in (
            {},
            {"query": "pump", "top_k": 101},
            {"query": "pump", "as_of": "2026-02-30"},
            {"references": [{"image": "https://example/image"}]},
        ):
            assert (await client.post("/visual/search", json=body)).status_code == 422
        assert (await client.post("/visual/rebuild")).json()[
            "indexed_figure_chunks"
        ] == 2
        rag.arebuild_visual_index.side_effect = RuntimeError("busy")
        assert (await client.post("/visual/rebuild")).status_code == 409
        rag._visual_runtime = None
        assert (
            await client.post("/visual/search", json={"query": "pump"})
        ).status_code == 503
