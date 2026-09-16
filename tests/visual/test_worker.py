import asyncio
import base64
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from ontorag.visual.client import VisualClient
from ontorag.visual.worker import WorkerBoundary, create_worker_app, open_image

pytestmark = pytest.mark.offline


async def test_worker_auth_and_errors():
    def embed(body):
        if body.get("bad"):
            raise ValueError("bad image")
        return {"space": "test", "vectors": [[1, 0]]}

    def rerank(body):
        raise RuntimeError("missing checkpoint")

    backend = SimpleNamespace(
        embed=embed, rerank=rerank, space="test", checkpoint_id=None
    )
    app = create_worker_app(backend, "worker-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker"
    ) as client:
        assert (await client.post("/embed", json={})).status_code == 401
        headers = {"Authorization": "Bearer worker-secret"}
        response = await client.post("/embed", json={"text": "pump"}, headers=headers)
        assert response.json()["vectors"] == [[1, 0]]
        assert (
            await client.post("/embed", json={"bad": True}, headers=headers)
        ).status_code == 422
        assert (
            await client.post("/rerank", json={}, headers=headers)
        ).status_code == 503


async def test_worker_body_limit_applies_to_streamed_requests():
    downstream = AsyncMock()
    boundary = WorkerBoundary(downstream, "key", max_bytes=3)
    messages = iter(
        [
            {"type": "http.request", "body": b"12", "more_body": True},
            {"type": "http.request", "body": b"34", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    send = AsyncMock()
    await boundary(
        {"type": "http", "headers": [(b"authorization", b"Bearer key")]}, receive, send
    )
    downstream.assert_not_awaited()
    assert send.call_args_list[0].args[0]["status"] == 413


async def test_worker_cancellation_does_not_release_gpu_slot_early():
    started, release = threading.Event(), threading.Event()
    calls = []

    def embed(body):
        calls.append(body["text"])
        if body["text"] == "first":
            started.set()
            assert release.wait(5)
        return {"space": "test", "vectors": [[1, 0]]}

    app = create_worker_app(SimpleNamespace(embed=embed, rerank=None), "key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://worker",
        headers={"Authorization": "Bearer key"},
    ) as client:
        first = asyncio.create_task(client.post("/embed", json={"text": "first"}))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            first.cancel()
            second = asyncio.create_task(client.post("/embed", json={"text": "second"}))
            await asyncio.sleep(0.03)
            assert calls == ["first"]
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second).status_code == 200
        assert calls == ["first", "second"]


def test_malformed_image_is_a_validation_error():
    with pytest.raises(ValueError, match="Invalid"):
        open_image(base64.b64encode(b"not an image").decode())


@pytest.mark.parametrize(
    "url,key",
    [
        ("", "key"),
        ("file:///tmp/image", "key"),
        ("https://user:pass@host", "key"),
        ("http://worker", ""),
    ],
)
def test_worker_client_requires_explicit_endpoint_and_key(monkeypatch, url, key):
    monkeypatch.delenv("VISUAL_WORKER_URL", raising=False)
    monkeypatch.delenv("VISUAL_WORKER_API_KEY", raising=False)
    with pytest.raises(ValueError):
        VisualClient(url, key)


@pytest.mark.parametrize(
    "result", [{"scores": []}, {"scores": [float("nan")]}, {"scores": [True]}]
)
async def test_invalid_scores_are_not_used(result):
    client = VisualClient("http://worker", "key")
    client.request = AsyncMock(return_value=result)
    with pytest.raises(ValueError):
        await client.rerank([{}], ["image"])


def test_partial_checkpoint_keeps_frozen_sam_weights_but_requires_trained_keys():
    from ontorag.visual.worker import validate_checkpoint_keys

    validate_checkpoint_keys(
        SimpleNamespace(
            unexpected_keys=[],
            missing_keys=["sam.retrieval_decoder.mask_tokens.weight"],
        )
    )
    for key in (
        "sam.image_encoder.trunk.blocks.1.adapter.up_proj.weight",
        "sam.retrieval_decoder.transformer.layers.0.self_attn.q_proj.weight",
        "sam.retrieval_decoder.pred_obj_score_head.layers.0.weight",
        "sam.retrieval_decoder.obj_score_token.weight",
    ):
        with pytest.raises(ValueError, match="missing trained"):
            validate_checkpoint_keys(
                SimpleNamespace(unexpected_keys=[], missing_keys=[key])
            )
