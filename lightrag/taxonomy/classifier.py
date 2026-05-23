"""Document-level YAGO classification.

Single LLM call per document. Candidates retrieved from the YAGO class
vector index; the LLM picks a weighted subset; the threshold rule from
docs/GraphAndRagArchitecture.md §5.4 filters down to the final assignment.

Failure modes (malformed JSON, LLM error, empty candidates, scores below
floor) all collapse to UNCATEGORIZED_IRI rather than raising — ingestion
must continue even when classification fails.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from lightrag.base import BaseVectorStorage
from lightrag.taxonomy.class_index import retrieve_candidate_classes
from lightrag.taxonomy.constants import (
    DEFAULT_CANDIDATE_COUNT,
    DEFAULT_MAX_CLASSES_PER_DOC,
    DEFAULT_MIN_SCORE,
    DEFAULT_SECONDARY_SCORE_RATIO,
    UNCATEGORIZED_IRI,
)

logger = logging.getLogger(__name__)

LLMFunc = Callable[..., Awaitable[str]]

_SYSTEM_PROMPT = (
    "You are a document classifier. Given a document and a list of "
    "candidate categories from the YAGO 4.5 taxonomy, return the "
    "categories that best describe the document's topical content. "
    "Reply with strict JSON and nothing else: "
    '{"assignments": [{"iri": "<iri>", "score": <float 0..1>}, ...]}. '
    "Use only IRIs from the provided candidates. Assign a higher score "
    "to categories that match the document's primary subject. Return an "
    "empty assignments list if no candidate fits."
)


def _format_user_prompt(doc_text: str, candidates: list[dict[str, Any]]) -> str:
    lines = ["Candidate categories:"]
    for c in candidates:
        label = c.get("label", "")
        iri = c.get("iri", "")
        lines.append(f"- {iri} ({label})")
    lines.append("")
    lines.append("Document:")
    lines.append(doc_text)
    return "\n".join(lines)


def _parse_llm_response(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        obj = json.loads(raw[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("response root must be an object")
    assignments = obj.get("assignments", [])
    if not isinstance(assignments, list):
        raise ValueError("`assignments` must be a list")
    return assignments


def _apply_threshold_rule(
    assignments: list[dict[str, Any]],
    *,
    max_classes: int,
    secondary_ratio: float,
    min_score: float,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for a in assignments:
        if not isinstance(a, dict):
            continue
        iri = a.get("iri")
        score = a.get("score")
        if not isinstance(iri, str) or not isinstance(score, (int, float)):
            continue
        cleaned.append({"iri": iri, "score": float(score)})

    cleaned.sort(key=lambda x: -x["score"])
    if not cleaned or cleaned[0]["score"] < min_score:
        return [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]

    top = cleaned[0]["score"]
    cutoff = top * secondary_ratio
    kept = [cleaned[0]]
    for a in cleaned[1:]:
        if a["score"] >= cutoff:
            kept.append(a)
        if len(kept) >= max_classes:
            break
    return kept[:max_classes]


class DocumentClassifier:
    """Per-document classifier wrapping candidate retrieval + LLM call."""

    def __init__(
        self,
        *,
        vector_storage: BaseVectorStorage,
        llm_func: LLMFunc,
        candidate_count: int = DEFAULT_CANDIDATE_COUNT,
        max_classes: int = DEFAULT_MAX_CLASSES_PER_DOC,
        secondary_ratio: float = DEFAULT_SECONDARY_SCORE_RATIO,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._vector = vector_storage
        self._llm = llm_func
        self._candidate_count = candidate_count
        self._max_classes = max_classes
        self._secondary_ratio = secondary_ratio
        self._min_score = min_score

    async def classify(self, doc_text: str) -> list[dict[str, Any]]:
        """Return the final assignment list `[{iri, score}, ...]`.

        Always returns at least one entry. If the LLM call or parse fails,
        or if no candidate clears `min_score`, returns the Uncategorized
        sentinel with score 0.0.
        """
        candidates = await retrieve_candidate_classes(
            doc_text, self._vector, top_n=self._candidate_count
        )
        if not candidates:
            return [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]

        prompt = _format_user_prompt(doc_text, candidates)
        try:
            raw = await self._llm(prompt, system_prompt=_SYSTEM_PROMPT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("YAGO classifier LLM call failed: %s", exc)
            return [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]

        try:
            assignments = _parse_llm_response(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("YAGO classifier response unparseable: %s", exc)
            return [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]

        candidate_iris = {c["iri"] for c in candidates}
        in_vocab = [a for a in assignments if a.get("iri") in candidate_iris]
        return _apply_threshold_rule(
            in_vocab,
            max_classes=self._max_classes,
            secondary_ratio=self._secondary_ratio,
            min_score=self._min_score,
        )
