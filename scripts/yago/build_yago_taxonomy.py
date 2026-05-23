"""Bootstrap the YAGO taxonomy layer in a LightRAG working directory.

Parses one or more YAGO N-Triples files, loads the class graph into the
yago_taxonomy namespace, selects a working vocabulary by descendant count,
and builds the YAGO class vector index against the working vocabulary.

This script directly instantiates the storage backends rather than booting
a full LightRAG instance — it doesn't need any of the LLM/embedding/role
machinery beyond `embedding_func`. The taxonomy lives in its own
namespaces and is consumed at query/ingest time by Plan B's wiring.

Usage:
    python scripts/yago/build_yago_taxonomy.py \\
        --files data/yago/2024-02-29/*.nt \\
        --working-dir ./rag_storage \\
        --workspace default \\
        --embedding-binding openai
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import sys
from pathlib import Path
from typing import Any

from lightrag.kg.nano_vector_db_impl import NanoVectorDBStorage
from lightrag.kg.networkx_impl import NetworkXStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy import (
    DEFAULT_WORKING_VOCABULARY_SIZE,
    build_class_index,
    load_taxonomy_to_graph,
    parse_ntriples_file,
    select_working_vocabulary,
)
from lightrag.utils import EmbeddingFunc

logger = logging.getLogger("yago.build")


async def build_taxonomy(
    *,
    files: list[Path],
    working_dir: Path,
    workspace: str,
    embedding_func: EmbeddingFunc,
    vocabulary_size: int = DEFAULT_WORKING_VOCABULARY_SIZE,
    excluded_iris: set[str] | None = None,
) -> dict[str, Any]:
    """Run the full bootstrap. Returns a small summary dict.

    Idempotent: re-running with the same inputs overwrites existing class
    nodes/edges and re-upserts the index records with identical content.
    """
    initialize_share_data()
    try:
        all_classes = []
        for f in files:
            all_classes.extend(parse_ntriples_file(f))

        # Dedupe by IRI; later files override earlier files for label/comment
        # so callers can layer per-language overlays later.
        by_iri: dict[str, Any] = {}
        for c in all_classes:
            existing = by_iri.get(c.iri)
            if existing is None:
                by_iri[c.iri] = c
            else:
                existing.parent_iris = sorted(
                    set(existing.parent_iris) | set(c.parent_iris)
                )
                if c.label:
                    existing.label = c.label
                if c.comment:
                    existing.comment = c.comment
        classes = list(by_iri.values())

        graph = NetworkXStorage(
            namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
            workspace=workspace,
            global_config={"working_dir": str(working_dir)},
            embedding_func=embedding_func,
        )
        await graph.initialize()
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
            logger.info("Loading %d classes into the YAGO graph namespace…", len(classes))
            await load_taxonomy_to_graph(classes, graph)

            logger.info("Selecting working vocabulary (target size %d)…", vocabulary_size)
            vocab = await select_working_vocabulary(
                graph,
                [c.iri for c in classes],
                target_size=vocabulary_size,
                excluded_iris=excluded_iris,
            )

            logger.info("Building class vector index over %d classes…", len(vocab))
            await build_class_index(vocab, graph, vdb)

            return {
                "classes_loaded": len(classes),
                "vocabulary_size": len(vocab),
                "index_records": len(vocab),
                "working_dir": str(working_dir),
                "workspace": workspace,
            }
        finally:
            await vdb.finalize()
            await graph.finalize()
    finally:
        finalize_share_data()


def _resolve_embedding(binding: str, model: str | None) -> EmbeddingFunc:
    """Resolve an EmbeddingFunc by binding name (e.g. 'openai', 'ollama').

    Mirrors how the API server resolves bindings — each binding exposes
    an `embed` callable wrapped with @wrap_embedding_func_with_attrs.
    """
    module = importlib.import_module(f"lightrag.llm.{binding}")
    embed_func = getattr(module, "embed", None) or getattr(module, "openai_embed", None)
    if embed_func is None:
        raise SystemExit(
            f"Embedding binding '{binding}' has no `embed` (or `openai_embed`)"
        )
    if isinstance(embed_func, EmbeddingFunc):
        return embed_func
    raise SystemExit(
        f"Resolved embedding from '{binding}' is not an EmbeddingFunc; "
        "wrap it with @wrap_embedding_func_with_attrs first"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--files", nargs="+", required=True, type=Path,
                   help="YAGO N-Triples files (schema + taxonomy)")
    p.add_argument("--working-dir", required=True, type=Path,
                   help="LightRAG working_dir to populate")
    p.add_argument("--workspace", default="default",
                   help="Workspace name (default: 'default')")
    p.add_argument("--vocabulary-size", type=int,
                   default=DEFAULT_WORKING_VOCABULARY_SIZE,
                   help=f"Working vocabulary size "
                        f"(default {DEFAULT_WORKING_VOCABULARY_SIZE})")
    p.add_argument("--embedding-binding", default="openai",
                   help="lightrag.llm.<binding> module to import")
    p.add_argument("--embedding-model", default=None,
                   help="Embedding model name (binding-specific)")
    p.add_argument("--exclude", action="append", default=[],
                   help="IRI to exclude from the vocabulary (repeatable)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    embed = _resolve_embedding(args.embedding_binding, args.embedding_model)
    summary = asyncio.run(build_taxonomy(
        files=args.files,
        working_dir=args.working_dir,
        workspace=args.workspace,
        embedding_func=embed,
        vocabulary_size=args.vocabulary_size,
        excluded_iris=set(args.exclude) or None,
    ))
    print("YAGO bootstrap complete:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
