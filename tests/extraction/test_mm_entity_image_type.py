"""The multimodal graph node injected for a drawing chunk is typed by the
VLM's image ``type`` (carried in ``sidecar.image_type``) instead of the
literal ``"drawing"``.

``extract_entities._process_single_content`` promotes every drawing / table /
equation chunk into a KG node named after the sidecar id.  Before this change
its ``entity_type`` was the structural kind (``"drawing"``), discarding the
classification the VLM had already produced (``Chart``, ``Flowchart``, ...).
Tables and equations are untouched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ontorag.operate import extract_entities
from ontorag.utils import Tokenizer, TokenizerInterface

pytestmark = pytest.mark.offline


class _CharTokenizer(TokenizerInterface):
    def encode(self, content: str):
        return [ord(ch) for ch in content]

    def decode(self, tokens):
        return "".join(chr(t) for t in tokens)


_EXTRACTION_RESULT = "(entity<|#|>ACME CORP<|#|>ORGANIZATION<|#|>A company)<|COMPLETE|>"


def _global_config() -> dict:
    extract_func = AsyncMock(return_value=_EXTRACTION_RESULT)
    return {
        "llm_model_func": extract_func,
        "role_llm_funcs": {
            "extract": extract_func,
            "keyword": extract_func,
            "query": extract_func,
            "vlm": extract_func,
        },
        "entity_extract_max_gleaning": 0,
        "entity_extract_max_records": 100,
        "entity_extract_max_entities": 40,
        "addon_params": {},
        "tokenizer": Tokenizer("char", _CharTokenizer()),
        "llm_model_max_async": 1,
    }


def _drawing_chunk(sidecar: dict) -> dict[str, dict]:
    content = (
        "[Image Name]q4_revenue_bar_chart\n[Image Type]Chart\n\n"
        "Bar chart of Acme Corp revenue by quarter."
    )
    return {
        "doc-1-mm-drawing-000": {
            "tokens": len(content),
            "content": content,
            "full_doc_id": "doc-1",
            "chunk_order_index": 0,
            "sidecar": sidecar,
        }
    }


async def _mm_node(monkeypatch, sidecar: dict) -> dict:
    monkeypatch.setenv("MAX_EXTRACT_INPUT_TOKENS", "999999")
    results = await extract_entities(
        chunks=_drawing_chunk(sidecar),
        global_config=_global_config(),
        pipeline_status={"latest_message": "", "history_messages": []},
    )
    maybe_nodes, _ = results[0]
    assert "d1" in maybe_nodes, sorted(maybe_nodes)
    return maybe_nodes["d1"][0]


async def test_mm_drawing_node_typed_from_sidecar_image_type(monkeypatch):
    node = await _mm_node(
        monkeypatch,
        {
            "type": "drawing",
            "id": "d1",
            "refs": [{"type": "drawing", "id": "d1"}],
            "image_type": "Chart",
        },
    )
    assert node["entity_type"] == "chart"


async def test_mm_drawing_node_falls_back_to_drawing_without_image_type(monkeypatch):
    """Legacy chunks (indexed before ``image_type`` existed) keep the
    structural type."""
    node = await _mm_node(
        monkeypatch,
        {"type": "drawing", "id": "d1", "refs": [{"type": "drawing", "id": "d1"}]},
    )
    assert node["entity_type"] == "drawing"


async def test_mm_drawing_node_image_type_normalized_like_extracted_types(monkeypatch):
    """Same normalization as LLM-extracted entity types: spaces removed,
    lower-cased -- so ``Chat Log`` lands as ``chatlog`` alongside them."""
    node = await _mm_node(
        monkeypatch,
        {
            "type": "drawing",
            "id": "d1",
            "refs": [{"type": "drawing", "id": "d1"}],
            "image_type": "Chat Log",
        },
    )
    assert node["entity_type"] == "chatlog"
