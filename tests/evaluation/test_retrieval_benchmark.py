import httpx
import pytest

from ontorag.evaluation.retrieval_benchmark import (
    evaluate_cases,
    ranking_metrics,
    summarize,
)


def test_rank_metrics_deduplicate_and_handle_missing_evidence():
    metrics = ranking_metrics(["wrong", "a", "a", "b"], ["a", "b"], 3)
    assert metrics["recall"] == 1
    assert metrics["reciprocal_rank"] == 0.5
    assert 0 < metrics["ndcg"] < 1
    assert ranking_metrics([], [], 10)["recall"] is None


async def test_benchmark_calls_real_contract_and_retains_labels():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "chunks": [
                        {
                            "chunk_id": "a",
                            "file_path": "manual.md",
                            "content": "90 days",
                        }
                    ]
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="http://test/", transport=httpx.MockTransport(handle)
    ) as client:
        rows = await evaluate_cases(
            client,
            [{"query": "maintenance", "expected_chunk_ids": ["a"]}],
            {"retrieval_top_k": 100},
        )
    assert requests[0].url.path == "/query/data"
    assert rows[0]["recall"] == 1
    assert summarize(rows)["p95_retrieval_seconds"] >= 0


async def test_backend_failure_is_not_scored_as_no_evidence():
    async with httpx.AsyncClient(
        base_url="http://test/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"status": "failure", "message": "backend down"}
            )
        ),
    ) as client:
        with pytest.raises(RuntimeError, match="backend down"):
            await evaluate_cases(
                client, [{"query": "maintenance", "expected_files": []}], {}
            )
