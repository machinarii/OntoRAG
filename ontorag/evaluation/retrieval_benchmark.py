"""Evaluate the real /query/data pipeline against human-labelled evidence.

    python -m ontorag.evaluation.retrieval_benchmark cases.json \
        --base-url http://localhost:9621 --output results.json

API credentials come from ONTORAG_API_KEY, never command-line arguments.
Cases name expected chunk IDs or source file paths in the indexed corpus.
No synthetic ranker or LLM judge is substituted for the retrieval pipeline.
"""

import argparse
import asyncio
import json
import math
import os
import time
from pathlib import Path

import httpx


def ranking_metrics(ranked, relevant, k):
    ranked = list(dict.fromkeys(ranked))[:k]
    relevant = set(relevant)
    gains = [int(item in relevant) for item in ranked]
    dcg = sum(gain / math.log2(i + 2) for i, gain in enumerate(gains))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return {
        "recall": sum(gains) / len(relevant) if relevant else None,
        "reciprocal_rank": next(
            (1 / (i + 1) for i, gain in enumerate(gains) if gain), 0
        )
        if relevant
        else None,
        "ndcg": dcg / ideal if ideal else None,
        "unexpected_evidence": bool(ranked) if not relevant else None,
    }


async def evaluate_cases(client, cases, options, k=10, answers=False):
    results = []
    for case in cases:
        request = {**options, **case.get("options", {}), "query": case["query"]}
        started = time.perf_counter()
        response = await client.post("query/data", json=request)
        response.raise_for_status()
        payload = response.json()
        if (
            payload.get("status") == "failure"
            and payload.get("metadata", {}).get("failure_reason") != "no_results"
        ):
            raise RuntimeError(
                f"Retrieval failed for {case['query']}: {payload.get('message')}"
            )
        chunks = payload.get("data", {}).get("chunks", [])
        field = "chunk_id" if "expected_chunk_ids" in case else "file_path"
        expected = case.get("expected_chunk_ids", case.get("expected_files", []))
        ranked = [chunk[field] for chunk in chunks if field in chunk]
        item = {
            "query": case["query"],
            "ranked": ranked,
            **ranking_metrics(ranked, expected, k),
            "retrieval_seconds": time.perf_counter() - started,
            "context_characters": sum(len(c.get("content", "")) for c in chunks),
            "processing_info": payload.get("metadata", {}).get("processing_info", {}),
        }
        if answers:
            answer_started = time.perf_counter()
            answer_response = await client.post(
                "query", json={**request, "stream": False, "include_references": True}
            )
            answer_response.raise_for_status()
            answer = answer_response.json()
            text = answer.get("response", "")
            item.update(
                answer=text, answer_seconds=time.perf_counter() - answer_started
            )
            expected_text = case.get("expected_answer_substrings")
            item["answer_matches_labels"] = (
                all(part.casefold() in text.casefold() for part in expected_text)
                if expected_text
                else None
            )
            abstained = answer.get("llm_generated") is False or text.startswith(
                (
                    "The retrieved evidence does not sufficiently support",
                    "The retrieved sources conflict",
                )
            )
            item["answered_unanswerable"] = (
                not abstained if case.get("unanswerable") else None
            )
            expected_files = set(case.get("expected_files", []))
            cited_files = {ref["file_path"] for ref in answer.get("references") or []}
            item["reference_recall"] = (
                len(expected_files & cited_files) / len(expected_files)
                if expected_files
                else None
            )
            # Cost is absent unless a deployment explicitly meters it; never
            # report fabricated zero-cost or token estimates as measurements.
            item["cost_usd"] = answer.get("cost_usd")
        results.append(item)
    return results


def summarize(rows):
    def mean(key):
        values = [row[key] for row in rows if row.get(key) is not None]
        return sum(values) / len(values) if values else None

    latencies = sorted(row["retrieval_seconds"] for row in rows)
    return {
        "cases": len(rows),
        **{
            key: mean(key)
            for key in (
                "recall",
                "reciprocal_rank",
                "ndcg",
                "context_characters",
                "answer_matches_labels",
                "answered_unanswerable",
                "reference_recall",
                "cost_usd",
            )
        },
        "p95_retrieval_seconds": latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)]
        if latencies
        else None,
    }


async def main(args):
    dataset = json.loads(Path(args.dataset).read_text())
    cases = dataset["cases"]
    if not cases or any(
        not c.get("query") or not ("expected_chunk_ids" in c or "expected_files" in c)
        for c in cases
    ):
        raise ValueError(
            "Every case needs query and labelled expected_chunk_ids or expected_files (empty for unanswerable)"
        )
    headers = (
        {"X-API-Key": os.environ["ONTORAG_API_KEY"]}
        if os.getenv("ONTORAG_API_KEY")
        else {}
    )
    profiles = dataset.get("profiles", {"baseline": {"mode": "mix"}})
    output = {"k": args.k, "profiles": {}}
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/") + "/", headers=headers, timeout=args.timeout
    ) as client:
        for name, options in profiles.items():
            rows = await evaluate_cases(client, cases, options, args.k, args.answers)
            output["profiles"][name] = {"summary": summarize(rows), "cases": rows}
    Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--base-url", default="http://localhost:9621")
    parser.add_argument("--output", default="retrieval-results.json")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--answers", action="store_true")
    args = parser.parse_args()
    if args.k < 1:
        parser.error("--k must be positive")
    asyncio.run(main(args))
