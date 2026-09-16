"""Run in a separate FoundYou environment: python -m ontorag.visual.worker.

The main OntoRAG service imports none of torch/transformers/FoundYou. This
worker uses the upstream model API directly, not its dataset/pickle CLI.
"""

import argparse
import asyncio
import hashlib
import hmac
import io
import os
from pathlib import Path
import sys
import warnings

import numpy as np

from ontorag.retrieval.index import _run_io
from .index import normalized
from .protocol import MAX_BATCH, decode_image, validate_references


def open_image(encoded):
    from PIL import Image, ImageOps

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(decode_image(encoded))) as source:
                if (
                    source.format not in {"PNG", "JPEG", "WEBP"}
                    or source.width * source.height > 20_000_000
                ):
                    raise ValueError(
                        "Use PNG, JPEG or WebP images of at most 20 megapixels"
                    )
                # Boxes refer to the displayed, EXIF-oriented image.
                return ImageOps.exif_transpose(source).convert("RGB")
    except (
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Invalid or oversized image") from exc


def crop_reference(ref):
    image = open_image(ref["image"])
    if ref.get("box"):
        x1, y1, x2, y2 = ref["box"]
        width, height = image.size
        box = (
            int(x1 * width),
            int(y1 * height),
            min(width, max(int(x1 * width) + 1, int(x2 * width))),
            min(height, max(int(y1 * height) + 1, int(y2 * height))),
        )
        image = image.crop(box)
    return image


def validate_checkpoint_keys(incompatible):
    # The published checkpoint contains only 167 trained tensors. Frozen mask
    # decoder weights are intentionally inherited from the SAM2 backbone.
    trained_prefixes = (
        "sam.retrieval_decoder.transformer.",
        "sam.retrieval_decoder.obj_score_token.",
        "sam.retrieval_decoder.pred_obj_score_head.",
    )
    if incompatible.unexpected_keys or any(
        ".adapter." in key or key.startswith(trained_prefixes)
        for key in incompatible.missing_keys
    ):
        raise ValueError(
            "Checkpoint is incompatible or missing trained FoundYou weights"
        )


class ModelBackend:
    def __init__(
        self,
        *,
        siglip_model,
        siglip_revision,
        foundyou_repo=None,
        checkpoint=None,
        device="cpu",
    ):
        import torch
        from transformers import AutoProcessor, SiglipModel

        self.torch, self.device = torch, device
        # Require an immutable model revision so an index cannot silently mix
        # two checkpoints with identical names and embedding dimensions.
        if len(siglip_revision) != 40 or any(
            c not in "0123456789abcdef" for c in siglip_revision
        ):
            raise ValueError("--siglip-revision must be a 40-character commit SHA")
        self.processor = AutoProcessor.from_pretrained(
            siglip_model, revision=siglip_revision, use_fast=False
        )
        self.siglip = (
            SiglipModel.from_pretrained(siglip_model, revision=siglip_revision)
            .eval()
            .to(device)
        )
        self.space = f"siglip:{siglip_model}@{siglip_revision}:cosine-v1"
        self.foundyou = None
        self.checkpoint_id = None
        if bool(foundyou_repo) != bool(checkpoint):
            raise ValueError("Supply both --foundyou-repo and --checkpoint")
        if foundyou_repo:
            self._load_foundyou(
                Path(foundyou_repo).resolve(), Path(checkpoint).resolve()
            )

    def _load_foundyou(self, repo, checkpoint):
        import yaml

        if (
            not (repo / "models/foundyou/__init__.py").is_file()
            or not checkpoint.is_file()
        ):
            raise ValueError("FoundYou checkout or checkpoint is missing")
        # Worker process isolation avoids FoundYou's generic top-level module
        # names (models, datasets, util) colliding with API dependencies.
        sys.path.insert(0, str(repo))
        from models.foundyou import build_foundyou
        from util.path_utils import SAM2_PATHS_CONFIG
        from util.promptable_utils import build_prompt_dict
        from torchvision import transforms

        config = repo / "configs/retrieval.yaml"
        settings = yaml.safe_load(config.read_text())
        for name, (weights, cfg) in list(SAM2_PATHS_CONFIG.items()):
            SAM2_PATHS_CONFIG[name] = (str(repo / weights), cfg)
        if not Path(SAM2_PATHS_CONFIG[settings["sam2_version"]][0]).is_file():
            raise ValueError(
                "Install the pretrained SAM2 backbone in FoundYou/pretrain before starting"
            )
        self.foundyou = build_foundyou(str(config))
        payload = self.torch.load(checkpoint, map_location="cpu", weights_only=True)
        weights = payload["model"]
        weights = {key.removeprefix("module."): value for key, value in weights.items()}
        incompatible = self.foundyou.load_state_dict(weights, strict=False)
        validate_checkpoint_keys(incompatible)
        self.foundyou.eval().to(self.device)
        self.prompt = build_prompt_dict
        # Match upstream retrieval preprocessing (opts.py defaults to 518).
        self.transform = transforms.Compose(
            [
                transforms.Resize((518, 518)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )
        digest = hashlib.sha256()
        with checkpoint.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        self.checkpoint_id = digest.hexdigest()

    def embed(self, body):
        images, text, references = (
            body.get("images"),
            body.get("text"),
            body.get("references"),
        )
        if sum(v is not None for v in (images, text, references)) != 1:
            raise ValueError("Supply exactly one of images, text or references")
        if references is not None:
            validate_references(references)
            inputs = self.processor(
                images=[crop_reference(ref) for ref in references], return_tensors="pt"
            ).to(self.device)
        elif images is not None:
            if not isinstance(images, list) or not 1 <= len(images) <= MAX_BATCH:
                raise ValueError("Supply 1–8 images")
            inputs = self.processor(
                images=[open_image(image) for image in images], return_tensors="pt"
            ).to(self.device)
        else:
            if not isinstance(text, str) or not 1 <= len(text) <= 4096:
                raise ValueError("Visual text query must contain 1–4096 characters")
            inputs = self.processor(
                text=[text], padding="max_length", truncation=True, return_tensors="pt"
            ).to(self.device)
        with self.torch.inference_mode():
            features = (
                self.siglip.get_text_features(**inputs)
                if text is not None
                else self.siglip.get_image_features(**inputs)
            )
        # transformers 4.x returns a tensor; newer versions may return a
        # BaseModelOutputWithPooling instead.
        if hasattr(features, "pooler_output"):
            features = features.pooler_output
        vectors = normalized(features.float().cpu().numpy())
        if references is not None:
            vectors = normalized([vectors.mean(axis=0)])
        return {"space": self.space, "vectors": vectors.tolist()}

    def rerank(self, body):
        if self.foundyou is None:
            raise RuntimeError("FoundYou checkpoint is not configured")
        references, images = body.get("references"), body.get("images")
        validate_references(references)
        if not isinstance(images, list) or not 1 <= len(images) <= MAX_BATCH:
            raise ValueError("Supply 1–8 candidate images")
        reference_tensors, prompts = [], []
        for ref in references:
            reference_tensors.append(
                self.transform(open_image(ref["image"])).to(self.device)
            )
            box = ref.get("box") or [0, 0, 1, 1]
            coordinates = self.torch.tensor(
                [min(517, float(v) * 518) for v in box], dtype=self.torch.float32
            )
            prompts.append(self.prompt(coordinates, "box", self.device))
        candidates = self.torch.stack(
            [self.transform(open_image(image)) for image in images]
        ).to(self.device)
        with self.torch.inference_mode():
            context = self.foundyou.encode_references(reference_tensors, prompts)
            scores = (
                self.foundyou.score_candidates(candidates, context)
                .float()
                .cpu()
                .tolist()
            )
        if not np.isfinite(scores).all():
            raise RuntimeError("FoundYou returned non-finite scores")
        return {"scores": scores, "checkpoint": self.checkpoint_id}


class WorkerBoundary:
    """Authenticate before parsing and cap both declared and streamed bodies."""

    def __init__(self, app, api_key, max_bytes=72 * 1024 * 1024):
        self.app, self.api_key, self.max_bytes = app, api_key, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse

        headers = dict(scope.get("headers", []))
        if not hmac.compare_digest(
            headers.get(b"authorization", b""), ("Bearer " + self.api_key).encode()
        ):
            return await JSONResponse({"detail": "Unauthorized"}, status_code=401)(
                scope, receive, send
            )
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse(
                {"detail": "Invalid Content-Length"}, status_code=400
            )(scope, receive, send)
        if declared_size > self.max_bytes:
            return await JSONResponse(
                {"detail": "Image request too large"}, status_code=413
            )(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                return await JSONResponse(
                    {"detail": "Image request too large"}, status_code=413
                )(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def buffered_receive():
            return messages.pop(0) if messages else await receive()

        await self.app(scope, buffered_receive, send)


def create_worker_app(backend, api_key):
    from fastapi import FastAPI, HTTPException

    if not api_key:
        raise ValueError("VISUAL_WORKER_API_KEY is required")
    app = FastAPI(title="OntoRAG visual worker")
    app.add_middleware(WorkerBoundary, api_key=api_key)
    lock = asyncio.Lock()

    async def run(method, body):
        async with lock:
            try:
                return await _run_io(lambda: method(body))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(503, "Visual model unavailable") from exc

    @app.post("/embed")
    async def embed(body: dict):
        return await run(backend.embed, body)

    @app.post("/rerank")
    async def rerank(body: dict):
        return await run(backend.rerank, body)

    @app.get("/health")
    async def health():
        return {"space": backend.space, "foundyou": backend.checkpoint_id}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--siglip-model", default="google/siglip-base-patch16-224")
    parser.add_argument("--siglip-revision", required=True)
    parser.add_argument("--foundyou-repo")
    parser.add_argument("--checkpoint")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9630)
    args = parser.parse_args()
    key = os.getenv("VISUAL_WORKER_API_KEY", "")
    if not key:
        parser.error("Set VISUAL_WORKER_API_KEY")
    if not 1 <= args.cpu_threads <= 64:
        parser.error("--cpu-threads must be between 1 and 64")
    if args.device == "cpu":
        import torch

        torch.set_num_threads(args.cpu_threads)
    backend = ModelBackend(
        siglip_model=args.siglip_model,
        siglip_revision=args.siglip_revision,
        foundyou_repo=args.foundyou_repo,
        checkpoint=args.checkpoint,
        device=args.device,
    )
    import uvicorn

    uvicorn.run(
        create_worker_app(backend, key),
        host=args.host,
        port=args.port,
        limit_concurrency=8,
    )


if __name__ == "__main__":
    main()
