"""Authenticated transport to a separately installed SigLIP/FoundYou worker."""

import math
import os
from urllib.parse import urlsplit

from .protocol import MAX_BATCH


class VisualClient:
    def __init__(self, url=None, api_key=None):
        self.url = url or os.getenv("VISUAL_WORKER_URL", "")
        self.api_key = api_key or os.getenv("VISUAL_WORKER_API_KEY", "")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("VISUAL_WORKER_URL must be an HTTP(S) worker URL")
        if not self.api_key:
            raise ValueError("VISUAL_WORKER_API_KEY is required")

    async def request(self, endpoint, body):
        import httpx

        # No client follows redirects or inherits proxy settings that could
        # forward image bytes/credentials to an unintended endpoint.
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            response = await client.post(
                self.url.rstrip("/") + endpoint,
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            return response.json()

    async def embed(self, *, images=None, text=None, references=None):
        if images is not None and len(images) > MAX_BATCH:
            raise ValueError("Visual embedding batch is too large")
        result = await self.request(
            "/embed", {"images": images, "text": text, "references": references}
        )
        vectors = result.get("vectors")
        expected = len(images) if images is not None else 1
        if (
            not isinstance(result.get("space"), str)
            or not result["space"]
            or not isinstance(vectors, list)
            or len(vectors) != expected
        ):
            raise ValueError("Invalid visual embedding response")
        return result["space"], vectors

    async def rerank(self, references, images):
        result = await self.request(
            "/rerank", {"references": references, "images": images}
        )
        scores = result.get("scores")
        if (
            not isinstance(scores, list)
            or len(scores) != len(images)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in scores)
        ):
            raise ValueError("Invalid FoundYou scores")
        return scores
