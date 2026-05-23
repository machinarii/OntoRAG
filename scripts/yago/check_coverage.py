"""Sample-based coverage check for the YAGO taxonomy layer.

Run this against a representative sample of your corpus (~100 docs)
before committing to Plan B (the LightRAG pipeline integration). If the
Uncategorized rate is >40-50%, the taxonomy needs domain-specific
overlays or a domain ontology — see docs/GraphAndRagArchitecture.md §5.7.

Usage:
    python scripts/yago/check_coverage.py \\
        --sample-dir ./corpus_sample \\
        --working-dir ./rag_storage \\
        --workspace default \\
        --llm-binding openai \\
        --embedding-binding openai
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import sys
from collections import Counter
from pathlib import Path

from lightrag.kg.nano_vector_db_impl import NanoVectorDBStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy import UNCATEGORIZED_IRI, DocumentClassifier
from lightrag.utils import EmbeddingFunc

logger = logging.getLogger("yago.coverage")


async def _check(
    *,
    sample_dir: Path,
    working_dir: Path,
    workspace: str,
    embedding_func: EmbeddingFunc,
    llm_func,
) -> None:
    initialize_share_data()
    try:
        vdb = NanoVectorDBStorage(
            namespace=NameSpace.VECTOR_STORE_YAGO_CLASSES,
            workspace=workspace,
            global_config={
                "working_dir": str(working_dir),
                "embedding_batch_num": 32,
                "vector_db_storage_cls_kwargs": {
                    "cosine_better_than_threshold": 0.0,
                },
            },
            embedding_func=embedding_func,
            meta_fields={"iri", "label", "content"},
        )
        await vdb.initialize()
        try:
            classifier = DocumentClassifier(
                vector_storage=vdb,
                llm_func=llm_func,
            )

            docs = sorted(p for p in sample_dir.iterdir() if p.is_file())
            if not docs:
                raise SystemExit(f"No files found under {sample_dir}")

            uncategorized = 0
            primary_counts: Counter[str] = Counter()
            for i, doc in enumerate(docs, start=1):
                text = doc.read_text(encoding="utf-8", errors="replace")
                assignments = await classifier.classify(text)
                top = assignments[0]
                if top["iri"] == UNCATEGORIZED_IRI:
                    uncategorized += 1
                else:
                    primary_counts[top["iri"]] += 1
                logger.info(
                    "[%d/%d] %s -> %s (%.2f)",
                    i, len(docs), doc.name, top["iri"], top["score"],
                )

            total = len(docs)
            print()
            print(f"=== Coverage report ({total} docs) ===")
            print(f"Uncategorized: {uncategorized}/{total} "
                  f"({100.0 * uncategorized / total:.1f}%)")
            print()
            print("Top primary classes:")
            for iri, count in primary_counts.most_common(20):
                print(f"  {count:4d}  {iri}")
        finally:
            await vdb.finalize()
    finally:
        finalize_share_data()


def _resolve_callable(binding: str, names: list[str]):
    module = importlib.import_module(f"lightrag.llm.{binding}")
    for n in names:
        fn = getattr(module, n, None)
        if fn is not None:
            return fn
    raise SystemExit(f"None of {names} found on lightrag.llm.{binding}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample-dir", required=True, type=Path)
    p.add_argument("--working-dir", required=True, type=Path)
    p.add_argument("--workspace", default="default")
    p.add_argument("--embedding-binding", default="openai")
    p.add_argument("--llm-binding", default="openai")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    embed = _resolve_callable(args.embedding_binding, ["embed", "openai_embed"])
    llm = _resolve_callable(args.llm_binding, [
        "complete", "openai_complete", "gpt_4o_mini_complete",
    ])
    asyncio.run(_check(
        sample_dir=args.sample_dir,
        working_dir=args.working_dir,
        workspace=args.workspace,
        embedding_func=embed,
        llm_func=llm,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
