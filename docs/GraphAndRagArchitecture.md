# Graph and RAG Architecture

Current architecture for LightRAG's knowledge graph and retrieval-augmented generation. Covers data model, storage backends, extraction pipeline, query modes, and context assembly. File:line citations point to the source of truth — read the code for full detail.

## 1. Component Map

```
            ┌──────────────────────────────────────────────────────┐
            │                    LightRAG class                    │
            │     (lightrag.py — composed from mixins, @final)     │
            └────────────────────────┬─────────────────────────────┘
                                     │
                  ┌──────────────────┼─────────────────────┐
                  ▼                  ▼                     ▼
          Ingestion (pipeline.py)  Query (aquery)     Migration / roles
                  │                  │
                  ▼                  ▼
        extract_entities      kg_query / naive_query
        (operate.py)          (operate.py)
                  │                  │
                  ▼                  ▼
        ┌─────────────────────────────────────────────────────────┐
        │            Four pluggable storage backends              │
        │  KV  •  Vector (entities/relations/chunks)  •  Graph    │
        │  •  Doc-status                                          │
        └─────────────────────────────────────────────────────────┘
```

The four storage types are coordinated by `LightRAG` and accessed by both the ingestion pipeline (writes) and the query operations (reads). All instances honor a `workspace` parameter for tenant/dataset isolation.

## 2. Storage Layer

### 2.1 Storage Types

| Type | Abstract base (`lightrag/base.py`) | Used for |
|------|------------------------------------|----------|
| KV   | `BaseKVStorage`                    | LLM response cache, chunk text, doc info |
| Vector | `BaseVectorStorage`              | Entity, relation, and chunk embeddings (3 separate vdbs) |
| Graph | `BaseGraphStorage` (`base.py:413-758`) | Entity–relation graph |
| Doc-status | `BaseDocStatusStorage`        | Document ingestion state machine |

### 2.2 Backend Registry

Resolution lives in `lightrag/kg/__init__.py` (`STORAGE_IMPLEMENTATIONS`, `STORAGES`, `STORAGE_ENV_REQUIREMENTS`) and `lightrag/kg/factory.py` (`get_storage_class`). The default JSON/NetworkX/NanoVectorDB backends are imported eagerly; all other backends load lazily via `importlib.import_module`.

| Slot | Backends |
|------|----------|
| Graph  | NetworkX, Neo4j, PostgreSQL (`PGGraphStorage`), MongoDB, Memgraph, OpenSearch |
| Vector | NanoVectorDB, Faiss, Milvus, Qdrant, PostgreSQL, MongoDB, Redis, OpenSearch |
| KV     | JSON, PostgreSQL, MongoDB, Redis |
| Doc-status | JSON, PostgreSQL, MongoDB |

### 2.3 Graph Data Model

Node fields (`base.py:413-758`):
- `entity_id` / `node_id`, `entity_type`, `description`
- `source_id` — pipe-separated chunk IDs that mention the entity
- `file_path`, `created_at`

Edge fields (undirected):
- `source_node_id`, `target_node_id`, `description`, `keywords`
- `weight` (default 1.0), `source_id`, `file_path`, `created_at`

Required `BaseGraphStorage` operations: `upsert_node`, `upsert_edge`, `get_node`, `get_edge`, `has_node`, `has_edge`, `node_degree`, `edge_degree`, `delete_node`, `remove_edges`, `get_knowledge_graph(node_label, max_depth, max_nodes)`, `get_popular_labels`, `search_labels`, plus batch variants (`upsert_nodes_batch`, `get_nodes_batch`, `get_edges_batch`, `node_degrees_batch`, `get_nodes_edges_batch`). The base provides serial defaults; SQL/graph-DB backends override with native batch operations.

### 2.4 Reference Backend: NetworkX

`lightrag/kg/networkx_impl.py:24-570`

- Storage layout: `{working_dir}/{workspace}/graph_{namespace}.graphml` (NetworkX-native GraphML).
- Concurrency: per-namespace `_storage_lock` from `get_namespace_lock`. A `storage_updated` flag (`get_update_flag`) signals other processes to reload before next read.
- Persistence: `index_done_callback()` writes the graph atomically under lock and detects inter-process updates (reloads instead of overwriting). Single-writer / multi-reader by contract.
- `get_knowledge_graph()` runs degree-prioritized BFS up to `max_depth` / `max_nodes` and reports a `truncated` flag.

### 2.5 Neo4j Backend

`lightrag/kg/neo4j_impl.py`

- Nodes carry their entity type as a Cypher label (e.g. `:PERSON`, `:ORG`) plus a `workspace`-tagged base label; edges use type-slugified relationship names (fallback `RELATED_TO`).
- Workspace isolation is enforced via a sanitized workspace label (`_get_workspace_label`) — backtick escaping guards against injection.
- All driver calls are wrapped in a retry decorator with exponential backoff (`ServiceUnavailable`, `SessionExpired`, `ConnectionResetError`).

### 2.6 Graph Utilities

`lightrag/utils_graph.py` exposes higher-level operations layered on top of `BaseGraphStorage` + the entity/relation vector DBs. All of these use `get_storage_keyed_lock` to keep graph + vdb writes atomic per entity/relation.

| Function | Purpose |
|----------|---------|
| `adelete_by_entity` | Remove node, incident edges, vdb records, chunk tracking. |
| `adelete_by_relation` | Bidirectional edge + vdb cleanup. |
| `aedit_entity`, `aedit_relation` | Update properties, support rename + collision/merge handling, re-embed. |
| `acreate_entity`, `acreate_relation` | Create with embeddings. |
| `amerge_entities` | Merge N sources into a target — consolidates fields per-strategy (`concatenate`, `keep_first`, `join_unique`, `max`, …) via `_merge_attributes`, relinks all relations, re-embeds. |
| `get_entity_info`, `get_relation_info` | Read view combining graph + vdb. |
| `_persist_graph_updates` | Fan-out `index_done_callback()` across modified storages. |

## 3. Ingestion → Graph

Entry point: `_PipelineMixin` in `lightrag/pipeline.py` (see the concurrency contract in `AGENTS.md`). For each chunk the extractor in `lightrag/operate.py` performs:

1. **LLM extraction** (`extract_entities` ≈ `operate.py:3232`, `_process_single_content` ≈ `:3320`)
   - Strips multimodal markup (`<cite>`, `<drawing>`, `<equation>`).
   - Calls the configured extraction LLM with prompts from `lightrag/prompt.py` (`entity_extraction_system_prompt`, `entity_extraction_user_prompt`, few-shot `entity_extraction_examples`).
   - Optional "gleaning" loop re-prompts up to `entity_extract_max_gleaning` times.
   - Caches responses in `llm_response_cache` (KV) keyed by content+config hash.
2. **Parsing** — JSON mode (`_process_json_extraction_result`) or delimited text mode (`_process_extraction_result`, `:3381-3396`).
3. **Merge across gleanings** (`:3398-3499`) — keep the richest description per entity/relation.
4. **Graph + VDB writes** (`_merge_nodes_then_upsert`, `_merge_edges_then_upsert`, `operate.py:2200-2740`):
   - `chunk_entity_relation_graph.upsert_node(name, data)`
   - `entities_vdb.upsert({id: {content, entity_name, entity_type, …}})`
   - `chunk_entity_relation_graph.upsert_edge(src, tgt, data)` — merging weights and descriptions when the edge already exists.
   - `relationships_vdb.upsert({id: {content, src_id, tgt_id, keywords, …}})`
   - `entity_chunks_storage` / `relation_chunks_storage` track which chunks mention each entity/relation to support incremental updates.

Persistence is deferred to `index_done_callback()` so a batch of upserts produces a single write per backend.

## 4. Query Path

### 4.1 Dispatch

`LightRAG.aquery` (`lightrag.py:1699`) is a back-compat wrapper around `aquery_llm` (`lightrag.py:1988-2051`):

```
mode in {local, global, hybrid, mix} → kg_query()        (operate.py:3659)
mode == "naive"                      → naive_query()     (operate.py:5575)
mode == "bypass"                     → direct LLM call   (lightrag.py:2010)
```

### 4.2 Modes

| Mode | What it pulls | High-level retrieval |
|------|---------------|----------------------|
| local  | Entities + their incident relations | low-level keywords → `entities_vdb` → graph node/edge expansion |
| global | Relations + their entities | high-level keywords → `relationships_vdb` → graph node fetch |
| hybrid | local + global combined | both branches |
| mix    | hybrid + chunk vector search; falls back to chunks if KG is empty | both branches + `chunks_vdb` |
| naive  | Chunks only | `chunks_vdb` → rerank → LLM |
| bypass | No retrieval | LLM only |

Mode-specific branching lives in `_perform_kg_search` (`operate.py:4195`); `_get_node_data` (`:4280`) handles local, `_get_edge_data` (`:4289`) handles global, and `_get_vector_context` is added for mix (`:4298`).

### 4.3 QueryParam

Defined in `lightrag/base.py:85-178`. Key fields:

- `mode`, `top_k`, `chunk_top_k`
- `max_entity_tokens`, `max_relation_tokens`, `max_total_tokens`
- `enable_rerank` (chunks only)
- `hl_keywords`, `ll_keywords` (precomputed keyword overrides)
- `response_type`, `stream`, `only_need_context`, `only_need_prompt`
- `conversation_history`, `user_prompt`

### 4.4 Context Assembly

`_build_query_context` (`operate.py:4861-4978`) drives the four stages, with `_build_context_str` (`:4678-4844`) producing the final string:

1. **Search** — `_perform_kg_search()` returns raw entities, relations, chunk candidates.
2. **Token truncation** — `_apply_token_truncation()` enforces `max_entity_tokens` and `max_relation_tokens`.
3. **Chunk merge** — `_merge_all_chunks()` deduplicates chunks pulled from graph traversal and from `chunks_vdb`, tracking frequency and source.
4. **Format** — assemble the prompt with sections for `Knowledge Graph Data (Entity)`, `Knowledge Graph Data (Relationship)`, `Document Chunks`, and a `Reference Document List`. Entities/relations are emitted as JSON-line records (`:4728-4733`); references are appended (`:4774`).

Token budget is dynamic:

```
available_chunk_tokens = max_total_tokens
                       - sys_prompt_tokens
                       - kg_context_tokens
                       - query_tokens
                       - buffer
```

The chunk list is then trimmed/reranked to fit (`:4709-4761`).

### 4.5 Reranking

`lightrag/rerank.py:182-281` and backends `cohere_rerank` (`:368`), `jina_rerank` (`:435`), `ali_rerank` (`:475`). Applied inside `process_chunks_unified` from `_build_context_str` (`:4764`). **Only text chunks are reranked** — entities and relations rely on their vdb similarity and graph degree, not the reranker.

### 4.6 Prompt Templates

`lightrag/prompt.py` (all under `PROMPTS[…]`):

| Template | Purpose |
|----------|---------|
| `entity_extraction_*`, `entity_extraction_examples` | Extraction-time prompts (ingest path). |
| `summarize_entity_descriptions` | Description merge during entity dedup. |
| `keywords_extraction` | Splits the user query into high-level + low-level keyword sets. |
| `rag_response` | Final-answer prompt for KG modes (entities + relations + chunks context). |
| `naive_rag_response` | Final-answer prompt for chunks-only mode. |
| `kg_query_context`, `naive_query_context` | Templates for the context block placeholder fill. |

### 4.7 Storage Interactions During a Query

```
kg_query / naive_query
    │
    ├─ keywords_extraction (LLM) ──► may hit llm_response_cache (KV)
    │
    ├─ entities_vdb.query(ll_keywords)            (local/hybrid/mix)
    ├─ relationships_vdb.query(hl_keywords)       (global/hybrid/mix)
    ├─ chunks_vdb.query(query)                    (naive/mix)
    │
    ├─ graph.get_nodes_batch / get_nodes_edges_batch / node_degrees_batch
    │
    ├─ text_chunks (KV).get_by_ids(chunk_ids)     ◄── full chunk text
    │
    ├─ _apply_token_truncation → _merge_all_chunks → _build_context_str
    │       (rerank chunks here, if enabled)
    │
    └─ LLM completion (skipped if only_need_context / only_need_prompt)
            └─ result cached back into llm_response_cache
```

Doc-status storage is **not** touched on the query path; it is read/written by the ingestion pipeline only.

## 5. YAGO Taxonomy Layer (Planned)

**Status:** design only — not yet implemented. Greenfield rebuild; no migration path from existing graphs (existing `rag_storage/` instances will be re-ingested).

### 5.1 Goals

Add hierarchical informational context to retrieval by classifying every ingested document into one or more [YAGO 4.5](https://yago-knowledge.org/downloads/yago-4-5) taxonomy classes. Chunks inherit their parent document's classes, giving the LLM topical framing alongside raw text. Enables browse/filter-by-class without a new query mode.

**Design stance — "Option A":** classes live at the *document* layer only. Extracted entities keep their free-form `entity_type` from LLM extraction; they are not linked to YAGO IRIs. This keeps the v1 surface small and ingestion cost flat; per-entity YAGO classification (Option B) is a deferred upgrade if eval shows entity-level hierarchy is needed.

### 5.2 Data Sources

- **YAGO 4.5 schema + taxonomy only** — the T-Box (~10K classes + `rdfs:subClassOf` edges + `rdfs:label` + `rdfs:comment`). Entity facts (A-Box) are not loaded.
- **Pinned version** of the dump (specific release date), cached locally. Version pinning prevents drift in extraction/classification behavior when YAGO updates.
- **Working vocabulary cap**: top ~200 classes selected for breadth and stability are exposed to the classifier; the full ~10K hierarchy is loaded into the graph for ancestor walks but not all of it is offered as a classification target. Fine-grained classes (e.g. `SerialKiller`) are excluded from the classifier's choice set because the LLM picks them inconsistently.

### 5.3 Storage Model

**Namespace separation.** YAGO classes live in a dedicated graph namespace (`yago_taxonomy` workspace or namespace prefix), distinct from the `chunk_entity_relation_graph` that holds extracted entities. This keeps `delete_by_entity`, `get_knowledge_graph` traversal, and entity counts clean — taxonomy reads cross namespaces explicitly when needed.

**Class node schema** (in the YAGO namespace):
- `entity_type = "YagoClass"`
- `class_iri` (canonical YAGO IRI)
- `label`, `comment` (from `rdfs:label` / `rdfs:comment`)
- `parent_class_iri` for fast single-hop lookups; full ancestry walked via `subClassOf` edges

**Class field on nodes — `list[str]`, not pipe-joined string.** Stored as a native list of class IRIs ordered leaf → root. Native list type in Neo4j / Memgraph / PostgreSQL (AGE) / MongoDB / OpenSearch; **NetworkX backend (`lightrag/kg/networkx_impl.py`) requires a JSON-encode/decode shim** because GraphML cannot round-trip list-valued attributes. Shim is isolated to `_encode_node()` / `_decode_node()` in that one file; the `BaseGraphStorage` contract stays `list[str]` for every consumer. Rationale: indexed membership queries (`"all entities under class X"`) are O(log n) on real backends with native lists, vs. O(n) substring scans on a pipe-string. See §2 of the design discussion for the full per-backend tradeoff.

**Document-level fields** (added to `doc_status` rows — see `lightrag/kg/json_doc_status_impl.py` and equivalents):
- `doc_categories: list[str]` — YAGO class IRIs assigned to this document, ordered by score (highest first), **capped at 10 entries**.
- `doc_category_scores: list[float]` — parallel list of confidence/relevance scores in [0, 1]. Stored even if v1 doesn't reason over them; backfilling later is expensive.
- Fallback sentinel: `lightrag:Uncategorized` — documents that don't fit any YAGO class. Always populated (never an empty list) so filter UX doesn't break.

**Denormalization onto chunks.** Each chunk row in `text_chunks` carries a copy of its parent document's `doc_categories` + `doc_category_scores`. Cost: tiny field, big query-path simplification (no extra batch fetch in the hot path). Re-ingestion is the only update mechanism, which matches the "redigest from scratch" policy.

### 5.4 Ingestion Pipeline Changes

A new **document-level classification step** slots into the ingestion pipeline after parsing and before chunking, run once per document:

1. Embed document content (or a representative summary if the doc is very long) against a new `yago_classes_vdb` index over class labels + `rdfs:comment`.
   - Must use the **same embedding model** as `entities_vdb` / `chunks_vdb` — switching embedding models forces a re-embedding of the YAGO class index too (per the embedding-model pitfall in `AGENTS.md`).
2. Retrieve top-N candidate classes by similarity (N ≈ 20 for the classifier prompt budget).
3. Single LLM call: "given this document and these candidate classes, select 1–10 classes that apply, with relevance scores in [0, 1]."
4. **Threshold rule**: always keep the top-scored class. Keep additional classes only if their score is ≥ 50% of the top score, up to 10 total. If no class scores above an absolute minimum (e.g. 0.3), assign `lightrag:Uncategorized`.
5. Persist to `doc_status` and propagate to all chunks of the document.

Per-chunk entity extraction (`extract_entities`, `operate.py:3232`) is **unchanged** in v1 — the doc-level taxonomy does not constrain or bias chunk-level extraction. This is intentional: it keeps ingestion cost flat versus the current baseline, and avoids tunnel-vision failure modes from passing doc classes into the extraction prompt.

**Cost profile** (vs. current ingestion): +1 embedding query and +1 LLM call per *document* (not per chunk). For a typical doc that splits into 50 chunks, this is a ~50× reduction in classification overhead compared to the per-chunk classification plan that was considered and rejected.

### 5.5 Query Path Changes

No new query mode. Taxonomy is folded into existing modes as **enrichment of local** (which also benefits `hybrid` and `mix` since they reuse `_get_node_data` at `operate.py:4280`).

**`QueryParam` additions** (`lightrag/base.py:85-178`):
- `doc_categories: list[str] | None = None` — optional filter. When set, only chunks whose parent doc's `doc_categories` intersects this list are eligible during chunk retrieval and merge. Implements browse-by-class with one new field.
- `taxonomy_enrichment: bool = False` (default off until eval validates) — gates the context-block additions described below.

**Context block changes** (`_build_context_str`, `operate.py:4678-4844`). Two additions when `taxonomy_enrichment=True`:

1. New leading sub-block, **Document Taxonomy Context**, listing the YAGO class paths of every document represented in the retrieved chunks. Class paths are capped at **3 ancestor levels** (leaf + 2 ancestors, e.g. `Drug → Medication → ChemicalSubstance`) to avoid diluting the LLM context with high-level `Thing` ancestors.
2. Per-chunk inline tag in the **Document Chunks** sub-block, showing the parent doc's primary class (top-scored entry from `doc_categories`).

Example rendered context:

```
Document Taxonomy Context:
  Doc[12]: Drug → Medication → ChemicalSubstance
  Doc[18]: Drug → Medication; ClinicalTrial → MedicalStudy
  Doc[31]: Person → Researcher → Scientist

Knowledge Graph Data (Entity):
  {entity_name: "Aspirin", entity_type: "DRUG", description: "...", ...}
  ...

Knowledge Graph Data (Relationship):
  ...

Document Chunks:
  [12] (Drug) "Aspirin reduces inflammation by inhibiting..."
  [18] (ClinicalTrial) "Patients in the trial received..."
```

**Token budgeting** lives inside the existing `max_entity_tokens` allocation in `_apply_token_truncation` (`operate.py:4904`). No new budget knob; if the Document Taxonomy Context overflows, it's trimmed from the bottom (lowest-frequency docs first).

**No changes to dispatch** in `aquery_llm` (`lightrag.py:1988-2051`) or `_perform_kg_search` (`operate.py:4195`). The enrichment is data-shaped — same code paths, richer payloads flowing through them.

### 5.6 Explicitly Out of Scope for v1

These are valid future upgrades; they are *not* part of the initial build:

- **Option B** — per-entity YAGO classification, sibling-expansion enrichment hook, and nearest-common-ancestor reranking of entities. Adds a per-entity LLM call; defer until eval shows the doc-level layer is insufficient.
- **Per-section classification** for long multi-topic documents (books, manuals). The native parser produces sections that could be classified individually; doing this would address the granularity loss noted below. Defer until corpus characteristics demand it.
- **Dedicated `taxonomy` query mode** for top-down "list all instances of class X" browsing without a vector seed. Possible once entity-level classes exist (Option B); not buildable on top of Option A alone.
- **Entity-linking to YAGO entity IRIs** (the A-Box). Would enable cross-document entity deduplication; significant cost; orthogonal to the topical-hierarchy goal.

### 5.7 Known Limitations of the v1 Design

- **Within-document topic shifts are flattened.** A long document covering 4 subtopics gets one shared class set; chunks in the middle of subtopic 3 carry the union. Mitigation: classify per section in a later iteration. Impact depends on corpus shape — low for papers/articles, higher for books/manuals.
- **No entity-level hierarchy.** Two extracted entities with the same free-form `entity_type` cannot be related through a class ancestor; the taxonomy connects them only by appearing in same-class documents (weaker signal). This is the Option-A tradeoff and is the most likely trigger for upgrading to Option B.
- **Coverage gap on domain corpora.** YAGO is general-knowledge. For specialized domains (medical, legal, internal-corp), expect a non-trivial share of documents to land in `lightrag:Uncategorized`. **Pre-build validation:** run doc-level classification on ≥100 representative corpus documents and measure the `Uncategorized` rate before committing to the full build. <40% clean mapping → reconsider with a domain-ontology overlay.

### 5.8 Pre-Build Spike Checklist

Plan A (infrastructure) shipped on branch `feat/yago-taxonomy-infrastructure` per `docs/superpowers/plans/2026-05-22-yago-taxonomy-infrastructure.md`. Items marked `[x] (Plan A)` are complete; the remainder gate Plan B.

- [x] (Plan A) Pin a specific YAGO 4.5 release; document the file list (schema + taxonomy only). — `scripts/yago/fetch_yago.sh`, default version `2024-02-29`.
- [x] (Plan A) Select the ~200-class working vocabulary (top-N by `subClassOf` descendant count, manually pruned for utility). — `lightrag/taxonomy/vocabulary.py::select_working_vocabulary`; manual exclusions via `--exclude` on the bootstrap CLI.
- [ ] Validate corpus coverage on a 100-doc sample; check `Uncategorized` rate. — run `python scripts/yago/check_coverage.py --sample-dir … --working-dir …` once Plan A bootstrap is complete on the target working directory. Gate: Uncategorized < 40-50%; otherwise add domain overlays before Plan B.
- [ ] Build an eval harness with a held-out query set + reference answers, run against current `main` *before* Plan B's taxonomy enrichment lands. Otherwise there's no apples-to-apples comparison once context formatting changes.
- [ ] Pick the document content used for classification (full text vs summary vs first-N tokens). The Plan A classifier accepts arbitrary text; Plan B chooses what to pass in.

## 6. Pointers

- Storage interfaces: `lightrag/base.py`
- Backend registry & resolution: `lightrag/kg/__init__.py`, `lightrag/kg/factory.py`
- Reference graph backend: `lightrag/kg/networkx_impl.py`
- Graph CRUD utilities: `lightrag/utils_graph.py`
- Extraction + query operations: `lightrag/operate.py`
- Ingestion pipeline: `lightrag/pipeline.py` (and concurrency contract in `AGENTS.md`)
- Prompts: `lightrag/prompt.py`
- Reranking: `lightrag/rerank.py`
- Query entry: `LightRAG.aquery` / `aquery_llm` in `lightrag/lightrag.py`
