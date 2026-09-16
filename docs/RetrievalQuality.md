# Retrieval quality controls

These additions are opt-in through the Python SDK and REST query schema. Existing
query defaults stay unchanged, except authoritative chunk hydration, concurrent
independent searches, and evidence-aware answer cache invalidation. Answer cache
policy v3 intentionally misses old entries: v2 entries cannot establish which
source text supported their answer. No deployed corpus has been benchmarked by
this change; tests establish behavior, not an accuracy gain.

## Enable and populate the derived index

Set `ENABLE_LEXICAL_INDEX=true` before starting the server, or instantiate
`OntoRAG(enable_lexical_index=True, ...)`, then call
`await rag.initialize_storages()`. For an existing corpus, call
`await rag.arebuild_retrieval_index()` or authenticated `POST /retrieval/rebuild`.
New ingestion and chunk deletion automatically maintain the index. Rebuild is
exclusive with ingestion, scans and deletes; a rejected reservation returns 409.
An incomplete rebuild refuses lexical searches until a rebuild succeeds.

The index lives at `working_dir/workspace/retrieval.sqlite3` and uses SQLite FTS5
BM25. It requires a persistent local directory shared by workers on ONE host;
SQLite WAL is not a multi-host or network-filesystem search service. Back up this
file with the corpus because it also contains explicitly assigned version
metadata. The text chunks remain the source of truth for returned evidence.
An existing corpus needs rebuilding when lexical indexing is first enabled.

`ENABLE_CONTEXTUAL_EMBEDDINGS=true` (SDK: `enable_contextual_embeddings=True`)
prefixes new embedding inputs with source filename and available heading ancestry.
It preserves the original chunk text. This is deterministic source context, not
LLM-generated contextual summaries. If the prefix would exceed the embedding
model's token limit, the source text takes precedence. Re-ingest into a fresh
workspace to change this setting consistently for an existing corpus; lexical
rebuild alone does not re-embed vectors. Do not mix contextual and plain vectors
when comparing evaluation runs.

## Query options

These fields work in `QueryParam` and `/query`, `/query/data`, `/query/stream`:

| Field | Default | Behavior |
|---|---|---|
| `enable_lexical` | false | Retrieve dense and BM25 candidates concurrently and fuse their ranks. Requires the index. |
| `fuse_retrieval` | false | RRF across dense, entity-derived and relation-derived chunk lists. |
| `retrieval_top_k` | existing chunk/top-k | Candidate count per dense/lexical branch, 1–1000. Graph entity/relation count still uses `top_k`. |
| `rerank_top_k` | existing chunk limit | Cap candidates sent to the configured reranker, 1–1000. |
| `chunk_top_k` | existing default | Final passage count, including expanded neighbors. |
| `rewrite_followups` | false | Rewrite ambiguous English follow-ups using up to six history messages, retain original-query chunk candidates, generate from the original question. |
| `context_neighbors` | 0 | Add up to 0–3 adjacent chunks on either side within the same document before final count/token limits. |
| `diversify_context` | false | Suppress near-duplicates and prefer coverage with a soft document penalty. |
| `document_version` | none | Match an explicit version label. |
| `as_of` | none | ISO date; requires known effective-from, effective-to is inclusive. |
| `exclude_superseded` | false | Exclude documents explicitly marked superseded. |
| `verify_answer` | false | Buffer generation, audit support/citations/conflicts, abstain on failed or malformed verification. |
| `retry_missing_evidence` | false | With verification, retry once using missing-evidence hints and a larger candidate budget. |
| `cache_retrieval` | false | Bounded 60-second candidate cache when the index supplies a revision. Always rehydrate and recheck filters. |
| `auto_route` | false | Route simple uppercase identifier lookups from mix to naive; retain graph mode for comparisons and relationship questions. |

Example request (configure a reranker separately to use reranking):

```json
{
  "query": "Compare PX-200 and PX-300 maintenance intervals",
  "mode": "mix",
  "enable_lexical": true,
  "fuse_retrieval": true,
  "retrieval_top_k": 80,
  "rerank_top_k": 40,
  "chunk_top_k": 12,
  "context_neighbors": 1,
  "diversify_context": true,
  "verify_answer": true,
  "retry_missing_evidence": true
}
```

Verifier output is an LLM judgment, not a calibrated confidence score. It adds a
model call; verified streaming is buffered so unsupported text is never emitted
before verification. A retry adds another retrieval/generation/verification pass.
Numerical citation IDs must occur in the supplied context, and the verifier must
report support without conflicts. Model errors propagate as errors; they are not
converted into evidence of an unanswerable question. Verification metadata is
available in responses. The answer cache is bypassed for verification and history.

Version-filtered queries retrieve source chunks and omit merged graph summaries,
which may mix revisions and cannot safely certify an as-of answer. Filtering takes
place after candidate retrieval, so very selective filters may need a larger
candidate budget. Unknown dates fail the as-of filter. Version labels and dates
are explicit metadata, never inferred from prose:

```http
PUT /retrieval/documents/DOCUMENT_ID/metadata
Content-Type: application/json

{"version":"v2","effective_from":"2026-01-01","effective_to":"2026-12-31","superseded":false}
```

This replaces metadata for the document; `{}` clears it. The SDK equivalent is
`await rag.aset_document_retrieval_metadata(doc_id, metadata)`.

## Evaluation

Create a labelled JSON dataset referencing actual indexed chunk IDs or file paths:

```json
{
  "profiles": {
    "baseline": {"mode":"mix", "enable_rerank":false},
    "enhanced": {"mode":"mix", "enable_lexical":true, "fuse_retrieval":true, "retrieval_top_k":80, "diversify_context":true, "verify_answer":true}
  },
  "cases": [
    {"query":"PX-200 maintenance interval?", "expected_files":["manual.md"], "expected_answer_substrings":["90 days"]},
    {"query":"What is the unpublished PX-999 interval?", "expected_files":[], "unanswerable":true}
  ]
}
```

Run `python -m ontorag.evaluation.retrieval_benchmark cases.json --answers
--output retrieval-results.json` against the running server. Set `ONTORAG_API_KEY`
in the environment if authentication is enabled. Measure final-context Recall@k,
MRR, nDCG, p95 retrieval latency, context characters, labelled answer substrings,
unanswerable false-answer rate and returned-reference recall. File-level recall
is coarser than chunk-level recall; returned references are not proof the answer
actually cites every source. Cost stays null unless metered by the deployment.
Include identifier, table, negation, ambiguous follow-up, comparison, contradictory
revision, and unanswerable cases. Compare identical corpora and model settings;
report cold/warm runs separately. Do not use this example's labels for your corpus.

## External designs reviewed (2026-09-15)

| Project / primary reference | Applied here | Boundary / follow-up |
|---|---|---|
| [Haystack DocumentJoiner](https://docs.haystack.deepset.ai/docs/documentjoiner) | Reciprocal-rank fusion without mixing incompatible raw scores | RRF is a candidate strategy; evaluate per corpus. |
| [Haystack SentenceWindowRetriever](https://docs.haystack.deepset.ai/docs/sentencewindowretriever) | Retrieve first, then add bounded neighboring source chunks | No new sentence-level index required. |
| [LlamaIndex Auto Merging Retriever](https://developers.llamaindex.ai/python/framework/integrations/retrievers/auto_merging_retriever/) | Small retrieval units with richer surrounding context informed neighbor expansion | This implementation does not claim hierarchical parent merging. |
| [RAGFlow](https://github.com/infiniflow/ragflow) | Preserve document structure and traceable source evidence through retrieval output | Existing native/MinerU/Docling parsers retained; no second ingestion engine. |
| [Anthropic contextual retrieval](https://www.anthropic.com/engineering/contextual-retrieval) | Context-prefixed embeddings plus lexical retrieval | Deterministic filename/headings only; no claim to reproduce their benchmark or generated contexts. |
| [FoundYou](https://github.com/ga1i13o/FoundYou) | SigLIP candidate retrieval followed by prompted FoundYou reranking, linked back to source chunks | Separately installed model worker; see [VisualRetrieval.md](VisualRetrieval.md). No face recognition or video crawler. |

[LightRAG releases](https://github.com/HKUDS/LightRAG/releases) still listed v1.5.7
as the latest published release at review time. Main was reviewed at `080f6ba`
(2026-09-15). The [Markdown table-header fix](https://github.com/HKUDS/LightRAG/commit/4af0d3870037188db1cc73e3468a635cf890ca1d)
is applied here with header-only, unsplit and split-table regression coverage.
Recent [OpenSearch missing-index read fixes](https://github.com/HKUDS/LightRAG/pull/3954)
and [Milvus async I/O changes](https://github.com/HKUDS/LightRAG/pull/3943)
are identified for backend-specific review, not silently merged into this fork's
storage and purge contracts. No upstream version bump is required for the applied
parser fix.
