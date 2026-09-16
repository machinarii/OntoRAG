"""Map indexed document figures to source passages without accepting file paths.

Only the server's persisted sidecar manifests resolve asset locations. Worker
requests contain bytes, never paths or URLs supplied by a query caller.
"""

import base64
import hashlib
import json
from pathlib import Path

from ontorag.retrieval.index import _run_io
from ontorag.retrieval.maintenance import maintenance_reservation
from ontorag.utils_pipeline import resolve_sidecar_uri, sidecar_modality_path

from .protocol import MAX_BATCH, MAX_IMAGE_BYTES


def drawing_refs(row):
    sidecar = row.get("sidecar") or {}
    refs = sidecar.get("refs") or [sidecar]
    return list(
        dict.fromkeys(
            ref["id"]
            for ref in refs
            if isinstance(ref, dict) and ref.get("type") == "drawing" and ref.get("id")
        )
    )


def load_manifest(uri):
    root = resolve_sidecar_uri(uri)
    manifest = sidecar_modality_path(uri, "drawings")
    if root is None or manifest is None:
        raise ValueError("Visual indexing requires a local document sidecar")
    path = Path(manifest)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Drawing manifest exceeds 16 MiB")
    drawings = json.loads(path.read_text(encoding="utf-8")).get("drawings")
    if not isinstance(drawings, dict):
        raise ValueError("Invalid drawing manifest")
    return root.resolve(), drawings


def read_asset(root, drawing):
    value = drawing.get("path")
    if not isinstance(value, str) or not value:
        raise ValueError("Drawing has no local image asset")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Drawing asset must be a file within its document sidecar")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Drawing asset must be at most 4 MiB")
    return base64.b64encode(data).decode("ascii"), hashlib.sha256(data).hexdigest()


class VisualRuntime:
    def __init__(self, rag, index, client):
        self.rag, self.index, self.client = rag, index, client

    async def manifest(self, doc_id):
        doc = await self.rag.full_docs.get_by_id(doc_id)
        if doc is None:
            raise ValueError("Visual figure has no source document")
        return await _run_io(lambda: load_manifest(doc.get("sidecar_location")))

    async def upsert(self, rows):
        manifests, assets, links = {}, {}, []
        for chunk_id, row in rows.items():
            refs = drawing_refs(row)
            if not refs:
                continue
            doc_id = row.get("full_doc_id")
            if not doc_id:
                raise ValueError("Visual chunk requires full_doc_id")
            if doc_id not in manifests:
                manifests[doc_id] = await self.manifest(doc_id)
            root, drawings = manifests[doc_id]
            for drawing_id in refs:
                if drawing_id not in drawings:
                    raise ValueError("Visual chunk references a missing drawing")
                # Empty paths are the parser's explicit representation of an
                # unavailable external image. There are no pixels to index.
                if not drawings[drawing_id].get("path"):
                    continue
                key = hashlib.sha256(
                    json.dumps([doc_id, drawing_id]).encode()
                ).hexdigest()
                assets[key] = dict(
                    id=key,
                    doc_id=doc_id,
                    drawing_id=drawing_id,
                    root=root,
                    drawing=drawings[drawing_id],
                )
                links.append((chunk_id, key))
        asset_rows, vectors, space = list(assets.values()), [], None
        for offset in range(0, len(asset_rows), MAX_BATCH):
            batch = asset_rows[offset : offset + MAX_BATCH]
            images = []
            for asset in batch:
                encoded, digest = await _run_io(
                    lambda asset=asset: read_asset(asset["root"], asset["drawing"])
                )
                asset["digest"] = digest
                images.append(encoded)
            batch_space, batch_vectors = await self.client.embed(images=images)
            if space is not None and batch_space != space:
                raise ValueError(
                    "Visual worker changed embedding space during ingestion"
                )
            space = batch_space
            vectors.extend(batch_vectors)
        await self.index.replace_chunks(list(rows), asset_rows, links, space, vectors)

    async def search(self, query, param):
        references = param.visual_references
        space, vectors = (
            await self.client.embed(references=references)
            if references
            else await self.client.embed(text=query)
        )
        hits = await self.index.search_vectors(vectors[0], space, param.visual_top_k)
        retrieval = self.rag._retrieval_runtime
        candidates, image_groups, manifests = [], {}, {}
        for hit in hits:
            rows = await retrieval.filter_chunks(
                await retrieval.hydrate(
                    [
                        {"chunk_id": key, "source_type": "visual"}
                        for key in hit["chunk_ids"]
                    ]
                ),
                param,
            )
            rows = [
                row
                for row in rows
                if row.get("full_doc_id") == hit["full_doc_id"]
                and hit["drawing_id"] in drawing_refs(row)
            ]
            if not rows:
                continue
            doc_id = hit["full_doc_id"]
            if doc_id not in manifests:
                manifests[doc_id] = await self.manifest(doc_id)
            root, drawings = manifests[doc_id]
            if hit["drawing_id"] not in drawings:
                raise ValueError("Visual index is stale; rebuild it")
            image, digest = await _run_io(
                lambda: read_asset(root, drawings[hit["drawing_id"]])
            )
            if digest != hit["digest"]:
                raise ValueError("Drawing content changed; rebuild the visual index")
            hit["image"] = image
            image_groups[hit["id"]] = rows
            candidates.append(hit)
        if references and param.visual_rerank:
            # Encode reference context per bounded worker request; no shared
            # mutable reference memory survives between users or workspaces.
            for offset in range(0, len(candidates), MAX_BATCH):
                batch = candidates[offset : offset + MAX_BATCH]
                scores = await self.client.rerank(
                    references, [hit["image"] for hit in batch]
                )
                for hit, score in zip(batch, scores):
                    hit["foundyou_score"] = score
            candidates.sort(key=lambda hit: hit["foundyou_score"], reverse=True)
        merged = {}
        for hit in candidates:
            match = {
                key: hit[key]
                for key in ("drawing_id", "visual_score", "foundyou_score")
                if key in hit
            }
            for row in image_groups[hit["id"]]:
                merged.setdefault(row["chunk_id"], {**row, "visual_matches": []})[
                    "visual_matches"
                ].append(match)
        # Rehydrate after potentially slow inference; deleted chunks cannot
        # reappear from the derived index or an in-flight worker response.
        return await retrieval.filter_chunks(
            await retrieval.hydrate(list(merged.values())), param
        )

    async def rebuild(self):
        from ontorag.base import DocStatus, CURSOR_START, CURSOR_END

        count = 0
        async with maintenance_reservation(self.rag):
            await self.index.set_ready(False)
            await self.index.clear()
            position = CURSOR_START
            while position is not CURSOR_END:
                page = await self.rag.doc_status.get_docs_by_statuses_page(
                    list(DocStatus), limit=100, position=position, strict=True
                )
                for doc_id in page.docs:
                    doc = await self.rag.doc_status.get_by_id(doc_id)
                    ids = (doc or {}).get("chunks_list", [])
                    for offset in range(0, len(ids), 128):
                        batch = ids[offset : offset + 128]
                        rows = await self.rag.text_chunks.get_by_ids(batch)
                        await self.upsert(
                            {
                                key: row
                                for key, row in zip(batch, rows)
                                if row is not None
                            }
                        )
                        count += sum(bool(drawing_refs(row)) for row in rows if row)
                position = page.next_position
            await self.index.set_ready(True)
        return {"indexed_figure_chunks": count, "revision": await self.index.revision()}


class VisualChunks:
    """Keep figure associations in the same mutation paths as source chunks."""

    def __init__(self, storage, runtime):
        self.storage, self.runtime = storage, runtime

    def __getattr__(self, name):
        return getattr(self.storage, name)

    async def upsert(self, rows):
        try:
            await self.storage.upsert(rows)
            await self.runtime.upsert(rows)
        except BaseException:
            await self.runtime.index.set_ready(False)
            raise

    async def delete(self, ids):
        try:
            await self.runtime.index.delete(ids)
            return await self.storage.delete(ids)
        except BaseException:
            await self.runtime.index.set_ready(False)
            raise

    async def drop(self):
        await self.runtime.index.set_ready(False)
        await self.runtime.index.clear()
        result = await self.storage.drop()
        if isinstance(result, dict) and result.get("status") == "error":
            return result
        await self.runtime.index.set_ready(True)
        return result
