"""A converter engine's ParseResult.canonical_source re-points the document.

The generated bundle becomes doc_status.file_path / metadata.source_file, the
enqueued name is kept as metadata.source_file_original, document_metadata is
merged into doc_status.metadata, the bundle is what gets archived, and the
original file is never moved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from ontorag import OntoRAG
from ontorag.base import DocStatus
from ontorag.constants import FULL_DOCS_FORMAT_PENDING_PARSE, PARSED_DIR_NAME
from ontorag.parser.registry import ParserSpec, register_parser
from ontorag.utils import EmbeddingFunc, Tokenizer

pytestmark = pytest.mark.offline


class _CharTokenizer:
    def encode(self, s):
        return [ord(c) for c in s]

    def decode(self, t):
        return "".join(chr(x) for x in t)


async def _emb(texts):
    return np.random.rand(len(texts), 32)


async def _llm(prompt, **kw):
    return "(entity<|#|>T<|#|>CONCEPT<|#|>desc)<|COMPLETE|>"


def _rag(tmp_path: Path) -> OntoRAG:
    return OntoRAG(
        working_dir=str(tmp_path / "wd"),
        workspace=f"cs-{tmp_path.name}",
        llm_model_func=_llm,
        embedding_func=EmbeddingFunc(embedding_dim=32, max_token_size=4096, func=_emb),
        tokenizer=Tokenizer("char", _CharTokenizer()),
    )


def _field(doc, name):
    return doc[name] if isinstance(doc, dict) else getattr(doc, name)


def test_canonical_source_repoints_doc_status_and_archives_bundle(
    tmp_path, monkeypatch
):
    register_parser(
        ParserSpec(
            engine_name="fakeconv",
            impl="tests.pipeline._fake_conv_parser:FakeConvParser",
            suffixes=frozenset({"pdf"}),
            queue_group="fakeconv",
            concurrency=1,
        )
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    monkeypatch.setenv("INPUT_DIR", str(inputs))
    src = inputs / "book.pdf"
    src.write_text("# Title\n\nbody text\n", encoding="utf-8")

    async def _run():
        rag = _rag(tmp_path)
        await rag.initialize_storages()
        try:
            track = await rag.apipeline_enqueue_documents(
                "",
                file_paths=str(src),
                docs_format=FULL_DOCS_FORMAT_PENDING_PARSE,
                parse_engine="fakeconv",
                process_options="F",
            )
            await rag.apipeline_process_enqueue_documents()
            docs = await rag.doc_status.get_docs_by_track_id(track)
            assert len(docs) == 1
            doc = next(iter(docs.values()))
            status = _field(doc, "status")
            assert status in (DocStatus.PROCESSED, DocStatus.PROCESSED.value)
            assert _field(doc, "file_path") == "book.textpack"
            meta = _field(doc, "metadata")
            assert meta["source_file"] == "book.textpack"
            assert meta["source_file_original"] == "book.pdf"
            assert meta["doc_type"] == "document"
            assert meta["bibliographic"] == {"title": "T"}
            # residency: original untouched, bundle archived
            assert src.exists()
            assert (inputs / PARSED_DIR_NAME / "book.textpack").exists()
        finally:
            await rag.finalize_storages()

    asyncio.run(_run())
