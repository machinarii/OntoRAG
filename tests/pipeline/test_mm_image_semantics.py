"""Image analysis semantics: ``subject`` / ``ocr_text`` and the ``image_type``
sidecar hint.

The VLM already interprets every embedded image into ``{name, type,
description}``.  These tests pin the additions that make an image
*classification-ready* for the taxonomy layer (docs/GraphAndRagArchitecture.md
§5) without yet wiring the classifier into ingestion (that is Plan B):

- ``subject``  (expected)  what the image is *about* -- as opposed to ``type``,
                           which is its presentational medium (Chart / Photo).
- ``ocr_text`` (optional)  verbatim text baked into the image, ``""`` if none.

Both are persisted into the sidecar ``llm_analyze_result`` and rendered into
the multimodal chunk so entity extraction sees OCR'd names and labels.

The ``image_type`` key is threaded into the chunk ``sidecar`` block so
``operate.extract_entities`` can type the multimodal graph node by medium
instead of the literal ``"drawing"`` (see tests/extraction/
test_mm_entity_image_type.py for that half).
"""

from __future__ import annotations

import asyncio
import json
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from ontorag import OntoRAG, RoleLLMConfig
from ontorag.chunk_schema import normalize_chunk_sidecar
from ontorag.operate import _parse_mm_display_name
from ontorag.utils import EmbeddingFunc, Tokenizer

pytestmark = pytest.mark.offline


class _CharTokenizer:
    def encode(self, content: str) -> list[int]:
        return [ord(ch) for ch in content]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens)


async def _mock_embedding(texts: list[str]) -> np.ndarray:
    return np.random.rand(len(texts), 32)


async def _mock_llm(prompt, **kwargs):
    return "{}"


def _new_rag(tmp_path: Path, vlm=None) -> OntoRAG:
    kwargs = {}
    if vlm is not None:
        kwargs["role_llm_configs"] = {"vlm": RoleLLMConfig(func=vlm)}
    return OntoRAG(
        working_dir=str(tmp_path),
        workspace=f"test-mm-image-semantics-{tmp_path.name}",
        llm_model_func=_mock_llm,
        embedding_func=EmbeddingFunc(
            embedding_dim=32, max_token_size=4096, func=_mock_embedding
        ),
        tokenizer=Tokenizer("char", _CharTokenizer()),
        vlm_process_enable=True,
        **kwargs,
    )


def _png(w: int, h: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    crc = zlib.crc32(b"IHDR" + ihdr).to_bytes(4, "big")
    return sig + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + crc


def _write_fixture(tmp_path: Path, analysis: dict | None = None):
    """Write a blocks.jsonl + drawings.json pair with one drawing ``d1``.

    ``analysis`` (when given) is stored as ``llm_analyze_result`` so the
    chunk builder can be driven without running the VLM.
    """
    img = tmp_path / "img1.png"
    img.write_bytes(_png(64, 64))
    blocks = tmp_path / "demo.blocks.jsonl"
    blocks.write_text(
        "\n".join(
            [
                json.dumps({"type": "meta", "format_version": "1.0"}),
                json.dumps({"type": "content", "content": "body"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    item = {"id": "d1", "caption": "Figure 1", "footnotes": [], "path": str(img)}
    if analysis is not None:
        item["llm_analyze_result"] = analysis
    drawings = tmp_path / "demo.drawings.json"
    drawings.write_text(
        json.dumps({"version": "1.0", "drawings": {"d1": item}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return blocks, drawings


def _parsed(blocks: Path) -> dict:
    return {
        "doc_id": "doc-1",
        "file_path": "demo.pdf",
        "blocks_path": str(blocks),
        "content": "body",
    }


def _success(**overrides) -> dict:
    base = {
        "name": "q4_revenue_bar_chart",
        "type": "Chart",
        "subject": "Acme Corp quarterly revenue, FY2025",
        "ocr_text": "Q1 1.2M\nQ2 1.4M\nQ3 1.1M\nQ4 2.0M",
        "description": "Bar chart of Acme Corp revenue by quarter.",
        "analyze_time": 1700000000,
        "status": "success",
        "message": "",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# analyze_multimodal: VLM response -> sidecar llm_analyze_result
# --------------------------------------------------------------------------


def test_analyze_multimodal_persists_subject_and_ocr_text(tmp_path):
    async def _run():
        async def _vlm(_prompt, **_kwargs):
            return json.dumps(
                {
                    "name": "q4_revenue_bar_chart",
                    "type": "Chart",
                    "subject": "Acme Corp quarterly revenue, FY2025",
                    "ocr_text": "Q1 1.2M\nQ4 2.0M",
                    "description": "Bar chart of revenue by quarter.",
                }
            )

        rag = _new_rag(tmp_path, vlm=_vlm)
        await rag.initialize_storages()
        blocks, drawings = _write_fixture(tmp_path)
        await rag.analyze_multimodal(
            "doc-1", "demo.pdf", _parsed(blocks), process_options="i"
        )

        result = json.loads(drawings.read_text(encoding="utf-8"))["drawings"]["d1"][
            "llm_analyze_result"
        ]
        assert result["status"] == "success"
        assert result["subject"] == "Acme Corp quarterly revenue, FY2025"
        assert result["ocr_text"] == "Q1 1.2M\nQ4 2.0M"
        await rag.finalize_storages()

    asyncio.run(_run())


def test_analyze_multimodal_missing_subject_folds_to_empty(tmp_path):
    """``subject`` follows the ``type`` precedent for non-conforming values
    (out-of-enum ``type`` folds to ``Other`` rather than failing): a missing
    or non-string ``subject`` lands as ``""`` with a warning, on the first
    VLM call -- it must not trigger the JSON-conformance retry nor fail the
    document.  The chunk builder already tolerates an absent subject, so
    the image simply is not classification-ready until re-analyzed."""

    async def _run():
        calls = {"n": 0}

        async def _vlm(_prompt, **_kwargs):
            calls["n"] += 1
            return json.dumps(
                {"name": "fig", "type": "Chart", "description": "details"}
            )

        rag = _new_rag(tmp_path, vlm=_vlm)
        await rag.initialize_storages()
        blocks, drawings = _write_fixture(tmp_path)
        await rag.analyze_multimodal(
            "doc-1", "demo.pdf", _parsed(blocks), process_options="i"
        )

        result = json.loads(drawings.read_text(encoding="utf-8"))["drawings"]["d1"][
            "llm_analyze_result"
        ]
        assert calls["n"] == 1
        assert result["status"] == "success"
        assert result["subject"] == ""
        await rag.finalize_storages()

    asyncio.run(_run())


def test_analyze_multimodal_omitted_ocr_text_defaults_to_empty(tmp_path):
    """Most images carry no baked-in text; a missing ``ocr_text`` is not a
    conformance failure and lands as ``""``."""

    async def _run():
        async def _vlm(_prompt, **_kwargs):
            return json.dumps(
                {
                    "name": "eiffel_tower_photo",
                    "type": "Photo",
                    "subject": "Eiffel Tower, Paris",
                    "description": "A photo of the Eiffel Tower.",
                }
            )

        rag = _new_rag(tmp_path, vlm=_vlm)
        await rag.initialize_storages()
        blocks, drawings = _write_fixture(tmp_path)
        await rag.analyze_multimodal(
            "doc-1", "demo.pdf", _parsed(blocks), process_options="i"
        )

        result = json.loads(drawings.read_text(encoding="utf-8"))["drawings"]["d1"][
            "llm_analyze_result"
        ]
        assert result["status"] == "success"
        assert result["ocr_text"] == ""
        await rag.finalize_storages()

    asyncio.run(_run())


def test_analyze_multimodal_non_string_ocr_text_folds_to_empty(tmp_path):
    async def _run():
        async def _vlm(_prompt, **_kwargs):
            return json.dumps(
                {
                    "name": "fig",
                    "type": "Chart",
                    "subject": "something",
                    "ocr_text": ["not", "a", "string"],
                    "description": "details",
                }
            )

        rag = _new_rag(tmp_path, vlm=_vlm)
        await rag.initialize_storages()
        blocks, drawings = _write_fixture(tmp_path)
        await rag.analyze_multimodal(
            "doc-1", "demo.pdf", _parsed(blocks), process_options="i"
        )

        result = json.loads(drawings.read_text(encoding="utf-8"))["drawings"]["d1"][
            "llm_analyze_result"
        ]
        assert result["status"] == "success"
        assert result["ocr_text"] == ""
        await rag.finalize_storages()

    asyncio.run(_run())


# --------------------------------------------------------------------------
# _build_mm_chunks_from_sidecars: llm_analyze_result -> multimodal chunk
# --------------------------------------------------------------------------


def _build(rag: OntoRAG, blocks: Path) -> dict:
    chunks = rag._build_mm_chunks_from_sidecars(
        doc_id="doc-1",
        file_path="demo.pdf",
        blocks_path=str(blocks),
        base_order_index=0,
    )
    assert len(chunks) == 1
    return chunks[0]


def test_mm_chunk_renders_subject_and_ocr_sections(tmp_path):
    """Subject joins the head block; OCR text becomes its own labelled
    section after the description so extraction sees the baked-in labels.
    VLM fields are sanitized like name/description (control chars would
    crash the GraphML flush)."""

    async def _run():
        rag = _new_rag(tmp_path)
        await rag.initialize_storages()
        blocks, _ = _write_fixture(tmp_path, _success(ocr_text="Q1 1.2M\x00\nQ4 2.0M"))
        chunk = _build(rag, blocks)
        content = chunk["content"]
        head, _, rest = content.partition("\n\n")
        assert head.splitlines() == [
            "[Image Name]q4_revenue_bar_chart",
            "[Image Type]Chart",
            "[Image Subject]Acme Corp quarterly revenue, FY2025",
        ]
        assert "Bar chart of Acme Corp revenue by quarter." in rest
        assert "[Image Text]Q1 1.2M\nQ4 2.0M" in rest
        assert "\x00" not in content
        # Description precedes OCR text.
        assert rest.index("Bar chart") < rest.index("[Image Text]")
        await rag.finalize_storages()

    asyncio.run(_run())


def test_mm_chunk_omits_ocr_section_when_empty(tmp_path):
    async def _run():
        rag = _new_rag(tmp_path)
        await rag.initialize_storages()
        blocks, _ = _write_fixture(tmp_path, _success(ocr_text=""))
        chunk = _build(rag, blocks)
        assert "[Image Text]" not in chunk["content"]
        assert "[Image Subject]" in chunk["content"]
        await rag.finalize_storages()

    asyncio.run(_run())


def test_mm_chunk_legacy_result_without_subject_still_builds(tmp_path):
    """Sidecars written before ``subject`` / ``ocr_text`` existed must keep
    building chunks on re-process; the new lines are simply absent."""

    async def _run():
        rag = _new_rag(tmp_path)
        await rag.initialize_storages()
        legacy = _success()
        del legacy["subject"]
        del legacy["ocr_text"]
        blocks, _ = _write_fixture(tmp_path, legacy)
        chunk = _build(rag, blocks)
        content = chunk["content"]
        assert content.startswith(
            "[Image Name]q4_revenue_bar_chart\n[Image Type]Chart\n\n"
        )
        assert "[Image Subject]" not in content
        assert "[Image Text]" not in content
        await rag.finalize_storages()

    asyncio.run(_run())


def test_mm_chunk_sidecar_carries_image_type(tmp_path):
    async def _run():
        rag = _new_rag(tmp_path)
        await rag.initialize_storages()
        blocks, _ = _write_fixture(tmp_path, _success(type="Flowchart"))
        chunk = _build(rag, blocks)
        assert chunk["sidecar"] == {
            "type": "drawing",
            "id": "d1",
            "refs": [{"type": "drawing", "id": "d1"}],
            "image_type": "Flowchart",
        }
        await rag.finalize_storages()

    asyncio.run(_run())


def test_mm_display_name_contract_survives_extra_head_lines(tmp_path):
    """``operate._parse_mm_display_name`` anchors on the ``[Image Name]``
    line; adding Subject / Text lines must not disturb it."""

    async def _run():
        rag = _new_rag(tmp_path)
        await rag.initialize_storages()
        blocks, _ = _write_fixture(tmp_path, _success())
        chunk = _build(rag, blocks)
        assert _parse_mm_display_name(chunk["content"], "d1") == "q4_revenue_bar_chart"
        await rag.finalize_storages()

    asyncio.run(_run())


def test_normalize_chunk_sidecar_preserves_image_type():
    dp = {
        "sidecar": {
            "type": "drawing",
            "id": "d1",
            "refs": [{"type": "drawing", "id": "d1"}],
            "image_type": "Chart",
        }
    }
    assert normalize_chunk_sidecar(dp) == {
        "type": "drawing",
        "id": "d1",
        "refs": [{"type": "drawing", "id": "d1"}],
        "image_type": "Chart",
    }


def test_normalize_chunk_sidecar_omits_image_type_when_absent():
    dp = {"sidecar": {"type": "table", "id": "t1"}}
    assert normalize_chunk_sidecar(dp) == {
        "type": "table",
        "id": "t1",
        "refs": [{"type": "table", "id": "t1"}],
    }
