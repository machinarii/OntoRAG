"""Reserved maintenance of the derived lexical index and document filters."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from uuid import uuid4


@asynccontextmanager
async def maintenance_reservation(rag):
    from ontorag.kg.shared_storage import (
        acquire_reservation,
        get_namespace_data,
        get_namespace_lock,
        release_owned_reservation,
        has_scan_deferred_processing,
    )

    status = await get_namespace_data("pipeline_status", workspace=rag.workspace)
    lock = get_namespace_lock("pipeline_status", workspace=rag.workspace)
    token = uuid4().hex
    acquired = cancelled = False
    try:
        result = await acquire_reservation(
            status,
            lock,
            owner_key="scanning_owner",
            owner=token,
            owner_kind="scan",
            flags={"scanning": True, "scanning_exclusive": True},
            reject_when=(
                ("busy", "Pipeline busy"),
                ("scanning", "Scan running"),
                ("pending_enqueues", "Enqueues pending"),
            ),
        )
        if not result.acquired:
            raise RuntimeError(result.message or "Pipeline reservation refused")
        acquired = True
        yield
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        await release_owned_reservation(
            rag.workspace,
            owner_key="scanning_owner",
            token=token,
            action=lambda state: state.update(
                scanning=False, scanning_exclusive=False, scanning_owner=None
            ),
        )
        # An SDK processing request refused by our exclusive scan fence leaves
        # sticky intent. Honour it after releasing, exactly as a scan does.
        if (
            acquired
            and not cancelled
            and await has_scan_deferred_processing(status, lock)
        ):
            from ontorag.utils import logger

            try:
                await rag.apipeline_process_enqueue_documents()
            except Exception as exc:
                logger.error("Post-maintenance queue drive failed: %s", exc)


def require_index(rag):
    runtime = getattr(rag, "_retrieval_runtime", None)
    if runtime is None or runtime.index is None:
        raise ValueError(
            "ENABLE_LEXICAL_INDEX=true and initialized storages are required"
        )
    return runtime.index


async def rebuild_index(rag):
    from ontorag.base import DocStatus, CURSOR_START, CURSOR_END

    index = require_index(rag)
    count = 0
    async with maintenance_reservation(rag):
        # Preserve explicit version metadata during a derived-index rebuild.
        await index.set_ready(False)
        await index.clear_chunks()
        position = CURSOR_START
        while position is not CURSOR_END:
            page = await rag.doc_status.get_docs_by_statuses_page(
                list(DocStatus),
                limit=100,
                position=position,
                strict=True,
            )
            for doc_id in page.docs:
                status = await rag.doc_status.get_by_id(doc_id)
                ids = (status or {}).get("chunks_list", [])
                for offset in range(0, len(ids), 256):
                    batch = ids[offset : offset + 256]
                    rows = await rag.text_chunks.get_by_ids(batch)
                    data = {
                        key: row for key, row in zip(batch, rows) if row is not None
                    }
                    await index.upsert(data)
                    count += len(data)
            position = page.next_position
        await index.set_ready(True)
    return {"indexed_chunks": count, "revision": await index.revision()}


async def set_document_metadata(rag, doc_id, metadata):
    index = require_index(rag)
    allowed = {"version", "effective_from", "effective_to", "superseded"}
    if set(metadata) - allowed:
        raise ValueError("Unknown document retrieval metadata")
    if "version" in metadata and (
        not isinstance(metadata["version"], str)
        or not 1 <= len(metadata["version"]) <= 200
    ):
        raise ValueError("version must be a nonempty string of at most 200 characters")
    if "superseded" in metadata and type(metadata["superseded"]) is not bool:
        raise ValueError("superseded must be a boolean")
    for key in ("effective_from", "effective_to"):
        if key in metadata:
            value = metadata[key]
            if (
                not isinstance(value, str)
                or date.fromisoformat(value).isoformat() != value
            ):
                raise ValueError(f"{key} must use YYYY-MM-DD")
    if (
        metadata.get("effective_from")
        and metadata.get("effective_to")
        and metadata["effective_from"] > metadata["effective_to"]
    ):
        raise ValueError("effective_to must not precede effective_from")
    async with maintenance_reservation(rag):
        if await rag.doc_status.get_by_id(doc_id) is None:
            raise KeyError(doc_id)
        await index.set_document_metadata(doc_id, metadata)
    return metadata
