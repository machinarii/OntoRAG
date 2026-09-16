"""Opt-in test with real, downloaded checkpoints; never used by offline CI."""

import base64
import io
import os

import numpy as np
import pytest


@pytest.mark.integration
def test_real_siglip_and_foundyou_checkpoint():
    repo = os.getenv("ONTORAG_TEST_FOUNDYOU_REPO")
    checkpoint = os.getenv("ONTORAG_TEST_FOUNDYOU_CHECKPOINT")
    if not repo or not checkpoint:
        pytest.skip(
            "Set ONTORAG_TEST_FOUNDYOU_REPO and ONTORAG_TEST_FOUNDYOU_CHECKPOINT"
        )
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from PIL import Image
    from ontorag.visual.worker import ModelBackend

    old_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        backend = ModelBackend(
            siglip_model="google/siglip-base-patch16-224",
            siglip_revision="7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed",
            foundyou_repo=repo,
            checkpoint=checkpoint,
            device="cpu",
        )
        buffer = io.BytesIO()
        Image.new("RGB", (96, 64), "red").save(buffer, format="PNG")
        image = base64.b64encode(buffer.getvalue()).decode()
        refs = [{"image": image, "box": [0.1, 0.1, 0.9, 0.9]}]
        for body in (
            {"images": [image]},
            {"text": "a red object"},
            {"references": refs},
        ):
            output = backend.embed(body)
            vector = np.asarray(output["vectors"])
            assert vector.shape == (1, 768)
            assert np.isfinite(vector).all()
            assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)
        output = backend.rerank({"references": refs, "images": [image]})
        assert len(output["scores"]) == 1 and np.isfinite(output["scores"]).all()
        assert (
            output["checkpoint"]
            == "9a56b19d7ff25889ee8f1a0db7b9118151d8c1c4ed272069184dad8e9214adf0"
        )
    finally:
        torch.set_num_threads(old_threads)
