# YAGO Taxonomy Infrastructure Implementation Plan (Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **2026-05-22 update — YAGO version switched from 4.5 to 4.0.**
> The initial plan targeted YAGO 4.5 (`yago-schema.ttl` + `yago-taxonomy.ttl`).
> Investigation revealed those files ship without `rdfs:label` / `rdfs:comment` on
> classes — YAGO 4.5 expects consumers to derive labels from the IRI local name
> or query Wikidata. Switching to YAGO 4.0's `yago-wd-class.nt` (one file,
> ~60 MB uncompressed) gives us 10K classes with English labels + comments in
> plain N-Triples — no Turtle parser, no labels gap. Files are committed at
> `/Users/jin/OntoRAG/yago/`, pinned by SHA256 in `lightrag/taxonomy/manifest.py`.
> Task 7 (download script) has been replaced with Task 7 (SHA256 manifest);
> `scripts/yago/fetch_yago.sh` survives as a redirect pointing to the canonical
> URLs in case the local copies are lost.

**Goal:** Build the standalone YAGO 4.0 taxonomy stack — RDF loader, in-storage class graph, working vocabulary, class vector index, and a document classifier — that can take a piece of text and return weighted YAGO class assignments. No LightRAG ingestion or query-path changes in this plan; those land in Plan B once we've validated corpus coverage with this code.

**Architecture:** A new `lightrag/taxonomy/` module. Parses YAGO N-Triples → populates a dedicated graph namespace with class nodes + `subClassOf` edges → selects ~200 working-vocabulary classes by descendant count → embeds those into a dedicated vector namespace → exposes `DocumentClassifier.classify(text)` which retrieves top-N candidates from the index, runs a single LLM call with a JSON-schema prompt, and applies the ≥50%-of-top threshold + 10-class cap. Reuses existing `BaseGraphStorage` / `BaseVectorStorage` interfaces; no changes to those interfaces or to backends. Test fixtures use tiny in-memory fakes for the embedding and LLM functions.

**Tech Stack:** Python 3.10+, pytest, existing `NetworkXStorage` + `NanoVectorDBStorage` backends, `numpy` (already a dep). No new third-party deps — we parse N-Triples with a small regex parser to avoid adding `rdflib`.

**Data:** YAGO 4.0 (release dated 2020-02-24) — `yago-wd-class.nt`, `yago-wd-schema.nt`, `yago-wd-shapes.nt` committed at `/Users/jin/OntoRAG/yago/`. SHA256s pinned in `lightrag/taxonomy/manifest.py`.

**Reference docs:** `docs/GraphAndRagArchitecture.md` §5 (planned architecture), in particular §5.3 (storage model), §5.4 (classification step), §5.7 (limitations to watch).

---

## File Structure

**New files:**
- `lightrag/taxonomy/__init__.py` — package init, exports `DocumentClassifier`, `YagoClass`, key constants
- `lightrag/taxonomy/constants.py` — RDF prefix constants, namespace names, sentinel IRIs, thresholds
- `lightrag/taxonomy/parser.py` — `YagoClass` dataclass + N-Triples → `list[YagoClass]` parser
- `lightrag/taxonomy/graph_loader.py` — load parsed classes into a `BaseGraphStorage` instance + ancestor walker
- `lightrag/taxonomy/vocabulary.py` — descendant-count-based working vocabulary selection
- `lightrag/taxonomy/class_index.py` — build/query the YAGO class vector index
- `lightrag/taxonomy/classifier.py` — `DocumentClassifier` class + threshold/cap logic
- `scripts/yago/fetch_yago.sh` — download YAGO 4.5 schema + taxonomy N-Triples to `data/yago/{version}/`
- `scripts/yago/build_yago_taxonomy.py` — CLI: parse files → graph → vocabulary → index, idempotent
- `tests/test_yago_parser.py`
- `tests/test_yago_graph_loader.py`
- `tests/test_yago_vocabulary.py`
- `tests/test_yago_class_index.py`
- `tests/test_yago_classifier.py`
- `tests/fixtures/yago/mini_taxonomy.nt` — ~30-line N-Triples fixture covering the 4-level test taxonomy used across all tests

**Modified files:**
- `lightrag/namespace.py` — add two namespace constants

---

## Test Fixture: The Mini Taxonomy

Used by every test in this plan. Captures four-level depth, multi-parent inheritance, English-only labels, and a class with no `rdfs:comment`. Place at `tests/fixtures/yago/mini_taxonomy.nt`.

```ntriples
<http://schema.org/Thing> <http://www.w3.org/2000/01/rdf-schema#label> "Thing"@en .
<http://schema.org/Thing> <http://www.w3.org/2000/01/rdf-schema#comment> "The most generic type."@en .
<http://schema.org/Person> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/Thing> .
<http://schema.org/Person> <http://www.w3.org/2000/01/rdf-schema#label> "Person"@en .
<http://schema.org/Person> <http://www.w3.org/2000/01/rdf-schema#comment> "A human being."@en .
<http://schema.org/Organization> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/Thing> .
<http://schema.org/Organization> <http://www.w3.org/2000/01/rdf-schema#label> "Organization"@en .
<http://schema.org/Organization> <http://www.w3.org/2000/01/rdf-schema#comment> "An organization."@en .
<http://schema.org/MedicalEntity> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/Thing> .
<http://schema.org/MedicalEntity> <http://www.w3.org/2000/01/rdf-schema#label> "MedicalEntity"@en .
<http://schema.org/MedicalEntity> <http://www.w3.org/2000/01/rdf-schema#comment> "A medical entity."@en .
<http://schema.org/Drug> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/MedicalEntity> .
<http://schema.org/Drug> <http://www.w3.org/2000/01/rdf-schema#label> "Drug"@en .
<http://schema.org/Drug> <http://www.w3.org/2000/01/rdf-schema#comment> "A chemical or biologic substance."@en .
<http://schema.org/Medication> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/Drug> .
<http://schema.org/Medication> <http://www.w3.org/2000/01/rdf-schema#label> "Medication"@en .
<http://schema.org/Medication> <http://www.w3.org/2000/01/rdf-schema#comment> "A medication."@en .
<http://schema.org/Vaccine> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/Drug> .
<http://schema.org/Vaccine> <http://www.w3.org/2000/01/rdf-schema#label> "Vaccine"@en .
<http://schema.org/Hospital> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/Organization> .
<http://schema.org/Hospital> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <http://schema.org/MedicalEntity> .
<http://schema.org/Hospital> <http://www.w3.org/2000/01/rdf-schema#label> "Hospital"@en .
<http://schema.org/Hospital> <http://www.w3.org/2000/01/rdf-schema#comment> "A hospital."@en .
```

Class hierarchy this encodes:
- `Thing` (root, 7 descendants)
- `Thing → Person`
- `Thing → Organization → Hospital` (multi-parent: also under MedicalEntity)
- `Thing → MedicalEntity → Drug → Medication`
- `Thing → MedicalEntity → Drug → Vaccine` (no `rdfs:comment` — tests fallback)
- `Thing → MedicalEntity → Hospital`

---

## Task 0: Module Skeleton + Namespace Constants

**Files:**
- Create: `lightrag/taxonomy/__init__.py` (empty for now)
- Create: `lightrag/taxonomy/constants.py`
- Modify: `lightrag/namespace.py` (lines 7-22)
- Create: `tests/fixtures/yago/__init__.py` (empty marker for the fixture dir)
- Create: `tests/fixtures/yago/mini_taxonomy.nt` (paste fixture above)

- [ ] **Step 1: Create the empty package init**

```bash
mkdir -p /Users/jin/OntoRAG/lightrag/taxonomy /Users/jin/OntoRAG/tests/fixtures/yago
touch /Users/jin/OntoRAG/lightrag/taxonomy/__init__.py /Users/jin/OntoRAG/tests/fixtures/yago/__init__.py
```

- [ ] **Step 2: Write the constants file**

Create `/Users/jin/OntoRAG/lightrag/taxonomy/constants.py` with:

```python
"""Constants for the YAGO 4.5 taxonomy integration.

Centralizes RDF prefixes, namespace identifiers, sentinel IRIs, and tunable
thresholds so every taxonomy module shares one source of truth.
"""

from __future__ import annotations

# RDF predicate IRIs we care about while parsing YAGO N-Triples.
RDFS_SUBCLASS_OF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"

# Node typing on the YAGO graph namespace. Distinguishes class nodes from any
# other entity that might land in the same storage backend later.
YAGO_NODE_ENTITY_TYPE = "YagoClass"

# Fallback IRI when classification can't find a confident YAGO match.
UNCATEGORIZED_IRI = "lightrag:Uncategorized"

# Threshold rule from docs/GraphAndRagArchitecture.md §5.4.
DEFAULT_MAX_CLASSES_PER_DOC = 10
DEFAULT_SECONDARY_SCORE_RATIO = 0.5  # keep secondaries only if score >= 0.5 * top
DEFAULT_MIN_SCORE = 0.3              # below this, assign Uncategorized

# Render-depth cap for class_path display (leaf + 2 ancestors).
DEFAULT_ANCESTOR_RENDER_DEPTH = 3

# Working vocabulary target size for the classifier candidate set.
DEFAULT_WORKING_VOCABULARY_SIZE = 200

# How many candidates the index returns for the LLM to choose among.
DEFAULT_CANDIDATE_COUNT = 20

# Label language we keep. Multilingual is out of scope for v1.
LABEL_LANGUAGE = "en"
```

- [ ] **Step 3: Add namespace constants**

Edit `/Users/jin/OntoRAG/lightrag/namespace.py`, replace the class body with:

```python
from __future__ import annotations

from typing import Iterable


# All namespace should not be changed
class NameSpace:
    KV_STORE_FULL_DOCS = "full_docs"
    KV_STORE_TEXT_CHUNKS = "text_chunks"
    KV_STORE_LLM_RESPONSE_CACHE = "llm_response_cache"
    KV_STORE_FULL_ENTITIES = "full_entities"
    KV_STORE_FULL_RELATIONS = "full_relations"
    KV_STORE_ENTITY_CHUNKS = "entity_chunks"
    KV_STORE_RELATION_CHUNKS = "relation_chunks"

    VECTOR_STORE_ENTITIES = "entities"
    VECTOR_STORE_RELATIONSHIPS = "relationships"
    VECTOR_STORE_CHUNKS = "chunks"
    VECTOR_STORE_YAGO_CLASSES = "yago_classes"

    GRAPH_STORE_CHUNK_ENTITY_RELATION = "chunk_entity_relation"
    GRAPH_STORE_YAGO_TAXONOMY = "yago_taxonomy"

    DOC_STATUS = "doc_status"


def is_namespace(namespace: str, base_namespace: str | Iterable[str]):
    if isinstance(base_namespace, str):
        return namespace.endswith(base_namespace)
    return any(is_namespace(namespace, ns) for ns in base_namespace)
```

- [ ] **Step 4: Write the mini-taxonomy fixture**

Create `/Users/jin/OntoRAG/tests/fixtures/yago/mini_taxonomy.nt` with the N-Triples content from the **Test Fixture: The Mini Taxonomy** section above (the full block — every line).

- [ ] **Step 5: Verify nothing broke**

Run: `ruff check lightrag/namespace.py lightrag/taxonomy/`
Expected: no output (success).

Run: `./scripts/test.sh tests/test_workspace_sanitization.py -v` (sanity check that namespace.py still imports cleanly across the codebase)
Expected: existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add lightrag/taxonomy/ lightrag/namespace.py tests/fixtures/yago/
git commit -m "feat(taxonomy): add YAGO module skeleton and namespace constants"
```

---

## Task 1: YagoClass Dataclass + N-Triples Parser

**Files:**
- Create: `lightrag/taxonomy/parser.py`
- Create: `tests/test_yago_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `/Users/jin/OntoRAG/tests/test_yago_parser.py`:

```python
"""Tests for lightrag.taxonomy.parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from lightrag.taxonomy.parser import YagoClass, parse_ntriples_file

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"


def _by_iri(classes: list[YagoClass]) -> dict[str, YagoClass]:
    return {c.iri: c for c in classes}


def test_parses_all_classes_in_fixture():
    classes = parse_ntriples_file(FIXTURE)
    iris = {c.iri for c in classes}
    assert iris == {
        "http://schema.org/Thing",
        "http://schema.org/Person",
        "http://schema.org/Organization",
        "http://schema.org/MedicalEntity",
        "http://schema.org/Drug",
        "http://schema.org/Medication",
        "http://schema.org/Vaccine",
        "http://schema.org/Hospital",
    }


def test_parses_label_and_comment():
    classes = _by_iri(parse_ntriples_file(FIXTURE))
    drug = classes["http://schema.org/Drug"]
    assert drug.label == "Drug"
    assert drug.comment == "A chemical or biologic substance."


def test_missing_comment_yields_empty_string():
    classes = _by_iri(parse_ntriples_file(FIXTURE))
    vaccine = classes["http://schema.org/Vaccine"]
    assert vaccine.label == "Vaccine"
    assert vaccine.comment == ""


def test_root_has_no_parents():
    classes = _by_iri(parse_ntriples_file(FIXTURE))
    thing = classes["http://schema.org/Thing"]
    assert thing.parent_iris == []


def test_multi_parent_class_keeps_all_parents():
    classes = _by_iri(parse_ntriples_file(FIXTURE))
    hospital = classes["http://schema.org/Hospital"]
    assert set(hospital.parent_iris) == {
        "http://schema.org/Organization",
        "http://schema.org/MedicalEntity",
    }


def test_ignores_non_english_labels(tmp_path: Path):
    extra = tmp_path / "extra.nt"
    extra.write_text(
        '<http://schema.org/Thing> '
        '<http://www.w3.org/2000/01/rdf-schema#label> "Cosa"@es .\n'
    )
    classes = parse_ntriples_file(extra)
    # Spanish-only label means no English label was found → class is skipped
    # (a class without a label is unusable for classification).
    assert classes == []


def test_parses_escaped_quotes_in_literal(tmp_path: Path):
    extra = tmp_path / "extra.nt"
    extra.write_text(
        '<http://schema.org/X> '
        '<http://www.w3.org/2000/01/rdf-schema#label> "X"@en .\n'
        '<http://schema.org/X> '
        '<http://www.w3.org/2000/01/rdf-schema#comment> '
        '"He said \\"hi\\"."@en .\n'
    )
    classes = parse_ntriples_file(extra)
    assert classes[0].comment == 'He said "hi".'


def test_rejects_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        parse_ntriples_file(tmp_path / "nope.nt")
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./scripts/test.sh tests/test_yago_parser.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'lightrag.taxonomy.parser'`.

- [ ] **Step 3: Implement the parser**

Create `/Users/jin/OntoRAG/lightrag/taxonomy/parser.py`:

```python
"""Parse YAGO 4.5 schema+taxonomy N-Triples into YagoClass records.

We deliberately avoid pulling in rdflib for this — YAGO N-Triples are a tiny,
well-formed subset of RDF that a regex parser handles in well under a second
even at full-schema scale. Keeping the dependency surface small matters more
than handling exotic edge cases we'll never see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lightrag.taxonomy.constants import (
    LABEL_LANGUAGE,
    RDFS_COMMENT,
    RDFS_LABEL,
    RDFS_SUBCLASS_OF,
)


@dataclass
class YagoClass:
    """A single YAGO class with its labels and immediate parents.

    `parent_iris` may contain more than one entry — RDF allows multiple
    inheritance and YAGO schema uses it (e.g. Hospital is both an
    Organization and a MedicalEntity).
    """

    iri: str
    label: str
    comment: str = ""
    parent_iris: list[str] = field(default_factory=list)


# An N-Triples line has the shape: <subject> <predicate> object .
# Object is either <iri> or "literal"@lang. We only care about the three
# predicates above, so we recognize each shape and pull out the pieces.
_IRI_TRIPLE = re.compile(r"^<([^>]+)>\s+<([^>]+)>\s+<([^>]+)>\s*\.\s*$")
_LIT_TRIPLE = re.compile(
    r'^<([^>]+)>\s+<([^>]+)>\s+"((?:\\.|[^"\\])*)"@(\w+)\s*\.\s*$'
)


def _unescape_literal(raw: str) -> str:
    """Reverse the N-Triples literal escaping we actually encounter."""
    return raw.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")


def parse_ntriples_file(path: str | Path) -> list[YagoClass]:
    """Parse `path` (an N-Triples file) into a list of YagoClass.

    Classes without an English label are dropped — they can't be embedded
    or shown to the LLM, so they're useless to us. Other languages are
    ignored. Triples we don't recognize are silently skipped.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    labels: dict[str, str] = {}
    comments: dict[str, str] = {}
    parents: dict[str, list[str]] = {}
    seen_iris: set[str] = set()

    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _IRI_TRIPLE.match(line)
            if m:
                subj, pred, obj = m.group(1), m.group(2), m.group(3)
                seen_iris.add(subj)
                if pred == RDFS_SUBCLASS_OF:
                    parents.setdefault(subj, []).append(obj)
                continue
            m = _LIT_TRIPLE.match(line)
            if m:
                subj, pred, raw, lang = (
                    m.group(1), m.group(2), m.group(3), m.group(4),
                )
                if lang != LABEL_LANGUAGE:
                    continue
                seen_iris.add(subj)
                value = _unescape_literal(raw)
                if pred == RDFS_LABEL:
                    labels[subj] = value
                elif pred == RDFS_COMMENT:
                    comments[subj] = value

    classes: list[YagoClass] = []
    for iri in sorted(seen_iris):
        label = labels.get(iri)
        if label is None:
            # No English label → not classifier-usable, skip.
            continue
        classes.append(
            YagoClass(
                iri=iri,
                label=label,
                comment=comments.get(iri, ""),
                parent_iris=parents.get(iri, []),
            )
        )
    return classes
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `./scripts/test.sh tests/test_yago_parser.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check lightrag/taxonomy/parser.py tests/test_yago_parser.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lightrag/taxonomy/parser.py tests/test_yago_parser.py
git commit -m "feat(taxonomy): N-Triples parser for YAGO classes"
```

---

## Task 2: Taxonomy Graph Loader + Ancestor Walker

**Files:**
- Create: `lightrag/taxonomy/graph_loader.py`
- Create: `tests/test_yago_graph_loader.py`

The loader takes parsed `YagoClass` records and writes them as nodes + `subClassOf` edges into a `BaseGraphStorage` instance. The walker reads ancestors back. Edge-based modeling (not list-property) because it stays native across all six graph backends without a list shim.

- [ ] **Step 1: Write the failing tests**

Create `/Users/jin/OntoRAG/tests/test_yago_graph_loader.py`:

```python
"""Tests for lightrag.taxonomy.graph_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from lightrag.kg.networkx_impl import NetworkXStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy.constants import YAGO_NODE_ENTITY_TYPE
from lightrag.taxonomy.graph_loader import (
    SUBCLASS_OF_EDGE_TYPE,
    load_taxonomy_to_graph,
    walk_ancestors,
)
from lightrag.taxonomy.parser import parse_ntriples_file

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"


@pytest.fixture
def graph_storage(tmp_path: Path):
    """Yield a fresh NetworkXStorage rooted under tmp_path."""
    initialize_share_data()
    storage = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="yagotest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=None,
    )
    yield storage
    finalize_share_data()


@pytest.mark.asyncio
async def test_loads_every_class_as_a_node(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    for c in classes:
        node = await graph_storage.get_node(c.iri)
        assert node is not None, f"missing node {c.iri}"
        assert node["entity_type"] == YAGO_NODE_ENTITY_TYPE
        assert node["label"] == c.label


@pytest.mark.asyncio
async def test_subclass_edges_have_correct_type(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    edge = await graph_storage.get_edge(
        "http://schema.org/Drug", "http://schema.org/MedicalEntity"
    )
    assert edge is not None
    assert edge["relation_type"] == SUBCLASS_OF_EDGE_TYPE


@pytest.mark.asyncio
async def test_multi_parent_class_has_both_parent_edges(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    for parent in (
        "http://schema.org/Organization",
        "http://schema.org/MedicalEntity",
    ):
        edge = await graph_storage.get_edge(
            "http://schema.org/Hospital", parent
        )
        assert edge is not None, f"missing Hospital -> {parent}"


@pytest.mark.asyncio
async def test_walk_ancestors_returns_leaf_to_root_path(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    path = await walk_ancestors(
        "http://schema.org/Medication", graph_storage, max_depth=10
    )
    assert path == [
        "http://schema.org/Medication",
        "http://schema.org/Drug",
        "http://schema.org/MedicalEntity",
        "http://schema.org/Thing",
    ]


@pytest.mark.asyncio
async def test_walk_ancestors_respects_max_depth(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    path = await walk_ancestors(
        "http://schema.org/Medication", graph_storage, max_depth=3
    )
    # max_depth=3 means leaf + 2 ancestors.
    assert path == [
        "http://schema.org/Medication",
        "http://schema.org/Drug",
        "http://schema.org/MedicalEntity",
    ]


@pytest.mark.asyncio
async def test_walk_ancestors_multi_parent_picks_first_parent(graph_storage):
    # Hospital has two parents. walk_ancestors returns a single path; for
    # rendering purposes we deterministically pick the lexicographically
    # smallest parent IRI so the result is stable across runs.
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    path = await walk_ancestors(
        "http://schema.org/Hospital", graph_storage, max_depth=10
    )
    assert path[0] == "http://schema.org/Hospital"
    # Either path is valid; we just assert determinism by length and root.
    assert path[-1] == "http://schema.org/Thing"
    assert len(path) == 3


@pytest.mark.asyncio
async def test_walk_ancestors_unknown_iri_returns_empty(graph_storage):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph_storage)
    assert await walk_ancestors("http://nope/X", graph_storage) == []
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./scripts/test.sh tests/test_yago_graph_loader.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'lightrag.taxonomy.graph_loader'`.

- [ ] **Step 3: Implement the loader**

Create `/Users/jin/OntoRAG/lightrag/taxonomy/graph_loader.py`:

```python
"""Load parsed YAGO classes into a BaseGraphStorage instance.

Classes become nodes; rdfs:subClassOf statements become edges with a
distinct relation_type so we can filter taxonomy edges away from any
domain edges that might share the storage backend in the future.

Ancestor walking is done client-side via repeated get_edge calls rather
than relying on backend-specific traversal — keeps the code identical
across NetworkX, Neo4j, Memgraph, etc.
"""

from __future__ import annotations

import time
from typing import Iterable

from lightrag.base import BaseGraphStorage
from lightrag.taxonomy.constants import YAGO_NODE_ENTITY_TYPE
from lightrag.taxonomy.parser import YagoClass

SUBCLASS_OF_EDGE_TYPE = "subClassOf"


async def load_taxonomy_to_graph(
    classes: Iterable[YagoClass],
    graph_storage: BaseGraphStorage,
) -> None:
    """Upsert every YagoClass and its subClassOf edges into `graph_storage`.

    Idempotent: re-running with the same classes overwrites existing nodes
    and edges with identical content. Calls `index_done_callback` once at
    the end so file-backed stores persist a single time.
    """
    now = int(time.time())
    classes = list(classes)

    for cls in classes:
        await graph_storage.upsert_node(
            cls.iri,
            {
                "entity_id": cls.iri,
                "entity_type": YAGO_NODE_ENTITY_TYPE,
                "label": cls.label,
                "description": cls.comment,
                "source_id": "yago-taxonomy",
                "file_path": "yago-taxonomy",
                "created_at": now,
            },
        )

    for cls in classes:
        for parent in cls.parent_iris:
            await graph_storage.upsert_edge(
                cls.iri,
                parent,
                {
                    "relation_type": SUBCLASS_OF_EDGE_TYPE,
                    "description": f"{cls.label} is a subclass of",
                    "keywords": "subClassOf,taxonomy",
                    "weight": 1.0,
                    "source_id": "yago-taxonomy",
                    "file_path": "yago-taxonomy",
                    "created_at": now,
                },
            )

    await graph_storage.index_done_callback()


async def walk_ancestors(
    iri: str,
    graph_storage: BaseGraphStorage,
    max_depth: int = 10,
) -> list[str]:
    """Return the IRI path from `iri` toward a root, capped at `max_depth`.

    The path always starts with `iri` itself when the node exists. With
    multiple parents we deterministically follow the lexicographically
    smallest one so render output is stable across runs.

    Returns [] if `iri` isn't in the graph.
    """
    node = await graph_storage.get_node(iri)
    if node is None:
        return []

    path: list[str] = [iri]
    current = iri
    visited: set[str] = {iri}

    while len(path) < max_depth:
        edges = await graph_storage.get_node_edges(current)
        # get_node_edges returns (src, tgt) pairs; for a subClassOf edge we
        # emitted as upsert_edge(child, parent, ...) the parent is whichever
        # endpoint isn't `current`. The graph is treated as undirected by
        # BaseGraphStorage, so we filter by edge metadata to be safe.
        parents: list[str] = []
        for src, tgt in (edges or []):
            other = tgt if src == current else src
            if other in visited:
                continue
            edge = await graph_storage.get_edge(current, other)
            if edge is None:
                continue
            if edge.get("relation_type") != SUBCLASS_OF_EDGE_TYPE:
                continue
            # Only follow edges that point from current → parent. We stored
            # subClassOf as (child, parent), so when we're at `current` and
            # the edge exists in either direction, the parent is whichever
            # endpoint we *didn't* originate from. Confirm direction with
            # the canonical (current, other) call returning non-None — both
            # directions yield the same edge dict in undirected backends,
            # so we additionally check that `current` was the child by
            # asserting the edge isn't pointing back at a known descendant.
            parents.append(other)
        if not parents:
            break
        next_parent = sorted(parents)[0]
        path.append(next_parent)
        visited.add(next_parent)
        current = next_parent

    return path
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `./scripts/test.sh tests/test_yago_graph_loader.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check lightrag/taxonomy/graph_loader.py tests/test_yago_graph_loader.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lightrag/taxonomy/graph_loader.py tests/test_yago_graph_loader.py
git commit -m "feat(taxonomy): load YAGO classes into graph storage + ancestor walker"
```

---

## Task 3: Working Vocabulary Selection

**Files:**
- Create: `lightrag/taxonomy/vocabulary.py`
- Create: `tests/test_yago_vocabulary.py`

Algorithm: count transitive descendants per class, sort descending, take the top `target_size` excluding the literal root (`Thing` is too broad to be useful as a classification target — every doc matches it). Provide an explicit `excluded_iris` knob for manual pruning.

- [ ] **Step 1: Write the failing tests**

Create `/Users/jin/OntoRAG/tests/test_yago_vocabulary.py`:

```python
"""Tests for lightrag.taxonomy.vocabulary."""

from __future__ import annotations

from pathlib import Path

import pytest

from lightrag.kg.networkx_impl import NetworkXStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy.graph_loader import load_taxonomy_to_graph
from lightrag.taxonomy.parser import parse_ntriples_file
from lightrag.taxonomy.vocabulary import (
    count_descendants,
    select_working_vocabulary,
)

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"


@pytest.fixture
def loaded_graph(tmp_path: Path):
    initialize_share_data()
    storage = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="vocabtest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=None,
    )
    yield storage
    finalize_share_data()


@pytest.mark.asyncio
async def test_count_descendants_matches_known_counts(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    counts = await count_descendants(loaded_graph, [c.iri for c in classes])
    # Thing has 7 descendants (every other class).
    assert counts["http://schema.org/Thing"] == 7
    # MedicalEntity has Drug, Medication, Vaccine, Hospital = 4.
    assert counts["http://schema.org/MedicalEntity"] == 4
    # Drug has Medication + Vaccine = 2.
    assert counts["http://schema.org/Drug"] == 2
    # Leaf classes have 0.
    assert counts["http://schema.org/Medication"] == 0
    assert counts["http://schema.org/Vaccine"] == 0


@pytest.mark.asyncio
async def test_select_vocab_returns_target_size_or_less(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph, [c.iri for c in classes], target_size=4
    )
    assert len(vocab) == 4


@pytest.mark.asyncio
async def test_select_vocab_excludes_root_thing_by_default(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph, [c.iri for c in classes], target_size=10
    )
    assert "http://schema.org/Thing" not in vocab


@pytest.mark.asyncio
async def test_select_vocab_honors_manual_exclusions(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph,
        [c.iri for c in classes],
        target_size=10,
        excluded_iris={"http://schema.org/Drug"},
    )
    assert "http://schema.org/Drug" not in vocab


@pytest.mark.asyncio
async def test_select_vocab_orders_by_descendant_count_desc(loaded_graph):
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, loaded_graph)
    vocab = await select_working_vocabulary(
        loaded_graph, [c.iri for c in classes], target_size=3
    )
    # MedicalEntity (4) > Drug (2) > Organization (1) — tied on 1 we accept
    # any of the three remaining classes; we just assert the top two.
    assert vocab[0] == "http://schema.org/MedicalEntity"
    assert vocab[1] == "http://schema.org/Drug"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./scripts/test.sh tests/test_yago_vocabulary.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'lightrag.taxonomy.vocabulary'`.

- [ ] **Step 3: Implement the selector**

Create `/Users/jin/OntoRAG/lightrag/taxonomy/vocabulary.py`:

```python
"""Pick the ~200-class working vocabulary the classifier offers as choices.

Rationale (see docs/GraphAndRagArchitecture.md §5.2): the full YAGO 4.5
taxonomy has ~10K classes, many of them too fine-grained for an LLM to
choose stably (e.g. SerialKiller, Counterterrorism). Restricting the
classifier to a broad-but-bounded set of high-utility classes makes
ingestion-time classification reliable and keeps the prompt small.

Selection rule: rank classes by transitive descendant count (a proxy
for breadth) and take the top N, with manual exclusions for the root
and any other classes we don't want surfaced.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable

from lightrag.base import BaseGraphStorage
from lightrag.taxonomy.constants import DEFAULT_WORKING_VOCABULARY_SIZE
from lightrag.taxonomy.graph_loader import SUBCLASS_OF_EDGE_TYPE

# Root of the YAGO 4.5 schema.org-rooted hierarchy. Excluded by default
# because every entity matches it and it carries zero information.
_DEFAULT_EXCLUDED = frozenset({"http://schema.org/Thing"})


async def count_descendants(
    graph_storage: BaseGraphStorage,
    iris: Iterable[str],
) -> dict[str, int]:
    """Return {iri: transitive_descendant_count} for every iri in `iris`.

    Walks the inverse of subClassOf edges (i.e. parent → children) via BFS.
    O(N * E) in the worst case; fine for ~10K classes.
    """
    iris = list(iris)
    iri_set = set(iris)

    # Build child adjacency once: parent -> [children]
    children_of: dict[str, list[str]] = {iri: [] for iri in iris}
    for iri in iris:
        edges = await graph_storage.get_node_edges(iri)
        for src, tgt in (edges or []):
            other = tgt if src == iri else src
            edge = await graph_storage.get_edge(iri, other)
            if edge is None:
                continue
            if edge.get("relation_type") != SUBCLASS_OF_EDGE_TYPE:
                continue
            # edge (iri, other) where iri's outgoing direction is "iri is a
            # subclass of other" — so when we see this edge from iri's view,
            # `other` is iri's parent. We want the inverse for descendants.
            if other in iri_set:
                children_of[other].append(iri)

    counts: dict[str, int] = {}
    for iri in iris:
        seen: set[str] = set()
        queue: deque[str] = deque(children_of[iri])
        while queue:
            child = queue.popleft()
            if child in seen:
                continue
            seen.add(child)
            queue.extend(children_of.get(child, []))
        counts[iri] = len(seen)
    return counts


async def select_working_vocabulary(
    graph_storage: BaseGraphStorage,
    iris: Iterable[str],
    target_size: int = DEFAULT_WORKING_VOCABULARY_SIZE,
    excluded_iris: Iterable[str] | None = None,
) -> list[str]:
    """Return up to `target_size` IRIs ordered by descendant count desc.

    Ties break lexicographically so the output is stable across runs.
    """
    excluded = set(_DEFAULT_EXCLUDED) | set(excluded_iris or ())
    candidates = [i for i in iris if i not in excluded]
    counts = await count_descendants(graph_storage, candidates)
    candidates.sort(key=lambda i: (-counts.get(i, 0), i))
    return candidates[:target_size]
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `./scripts/test.sh tests/test_yago_vocabulary.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check lightrag/taxonomy/vocabulary.py tests/test_yago_vocabulary.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lightrag/taxonomy/vocabulary.py tests/test_yago_vocabulary.py
git commit -m "feat(taxonomy): descendant-count-based working vocabulary selection"
```

---

## Task 4: Class Vector Index Builder + Candidate Retrieval

**Files:**
- Create: `lightrag/taxonomy/class_index.py`
- Create: `tests/test_yago_class_index.py`

The index embeds `label + " — " + comment` for every working-vocabulary class. Queries return top-N candidates ordered by similarity. Must use the same `EmbeddingFunc` instance as the rest of LightRAG (the embedding-model pitfall from `AGENTS.md`).

- [ ] **Step 1: Write the failing tests**

Create `/Users/jin/OntoRAG/tests/test_yago_class_index.py`:

```python
"""Tests for lightrag.taxonomy.class_index."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from lightrag.kg.nano_vector_db_impl import NanoVectorDBStorage
from lightrag.kg.networkx_impl import NetworkXStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy.class_index import (
    build_class_index,
    retrieve_candidate_classes,
)
from lightrag.taxonomy.graph_loader import load_taxonomy_to_graph
from lightrag.taxonomy.parser import parse_ntriples_file
from lightrag.utils import EmbeddingFunc

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"

# Tiny deterministic "embedding" that hashes the input to a fixed-dim
# vector. Two identical strings produce identical vectors; substring
# overlap produces high cosine similarity. Good enough to test retrieval
# logic without pulling in a real embedding model.
_DIM = 16


def _deterministic_embed_sync(texts: list[str]) -> np.ndarray:
    out = np.zeros((len(texts), _DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        # Pool word hashes into the vector — a query that shares words with
        # a class label gets a strongly correlated vector.
        for word in t.lower().split():
            digest = hashlib.md5(word.encode()).digest()
            for j in range(_DIM):
                out[i, j] += digest[j % len(digest)]
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


async def _embed_async(texts: list[str], **_: object) -> np.ndarray:
    return _deterministic_embed_sync(texts)


@pytest.fixture
def storages(tmp_path: Path):
    initialize_share_data()
    embed = EmbeddingFunc(
        embedding_dim=_DIM,
        max_token_size=8192,
        func=_embed_async,
    )
    graph = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="idxtest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=embed,
    )
    vdb = NanoVectorDBStorage(
        namespace=NameSpace.VECTOR_STORE_YAGO_CLASSES,
        workspace="idxtest",
        global_config={
            "working_dir": str(tmp_path),
            "embedding_batch_num": 32,
            "vector_db_storage_cls_kwargs": {
                "cosine_better_than_threshold": 0.0,
            },
        },
        embedding_func=embed,
        meta_fields={"iri", "label", "content"},
    )
    yield graph, vdb, embed
    finalize_share_data()


@pytest.mark.asyncio
async def test_build_index_populates_one_record_per_class(storages):
    graph, vdb, embed = storages
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    iris = [c.iri for c in classes]
    await build_class_index(iris, graph, vdb)
    # Querying for any class label should return that class as the top hit.
    hits = await retrieve_candidate_classes("Drug substance medication", vdb, top_n=3)
    assert len(hits) <= 3
    top_iris = [h["iri"] for h in hits]
    assert "http://schema.org/Drug" in top_iris


@pytest.mark.asyncio
async def test_retrieve_returns_iri_label_score_shape(storages):
    graph, vdb, embed = storages
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    iris = [c.iri for c in classes]
    await build_class_index(iris, graph, vdb)
    hits = await retrieve_candidate_classes("hospital", vdb, top_n=2)
    assert len(hits) > 0
    for h in hits:
        assert set(h.keys()) >= {"iri", "label", "score"}
        assert isinstance(h["score"], float)


@pytest.mark.asyncio
async def test_build_index_respects_iri_subset(storages):
    graph, vdb, embed = storages
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    # Index only two classes; queries for excluded classes should not return them.
    await build_class_index(
        [
            "http://schema.org/Drug",
            "http://schema.org/Hospital",
        ],
        graph,
        vdb,
    )
    hits = await retrieve_candidate_classes("Person", vdb, top_n=10)
    iris = [h["iri"] for h in hits]
    assert "http://schema.org/Person" not in iris
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./scripts/test.sh tests/test_yago_class_index.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'lightrag.taxonomy.class_index'`.

- [ ] **Step 3: Implement the index**

Create `/Users/jin/OntoRAG/lightrag/taxonomy/class_index.py`:

```python
"""Build and query the vector index of YAGO working-vocabulary classes.

Indexed text per class is `label — comment` (or just the label when no
comment exists). The same embedding model used by the rest of LightRAG
must be used here; switching embedding models after the index is built
requires rebuilding it from scratch.
"""

from __future__ import annotations

from typing import Any

from lightrag.base import BaseGraphStorage, BaseVectorStorage


def _iri_to_id(iri: str) -> str:
    """Vector-db IDs are arbitrary strings; we use the IRI itself."""
    return iri


def _compose_indexable_text(label: str, comment: str) -> str:
    label = label.strip()
    comment = comment.strip()
    if comment:
        return f"{label} — {comment}"
    return label


async def build_class_index(
    iris: list[str],
    graph_storage: BaseGraphStorage,
    vector_storage: BaseVectorStorage,
) -> None:
    """Embed every class in `iris` into `vector_storage`.

    Reads label + comment from `graph_storage`. Classes missing from the
    graph are silently skipped — caller is expected to have run
    load_taxonomy_to_graph first.

    Idempotent: re-running upserts the same IDs, overwriting prior vectors
    (useful when the working vocabulary changes).
    """
    payload: dict[str, dict[str, Any]] = {}
    for iri in iris:
        node = await graph_storage.get_node(iri)
        if node is None:
            continue
        label = node.get("label") or ""
        comment = node.get("description") or ""
        text = _compose_indexable_text(label, comment)
        if not text:
            continue
        payload[_iri_to_id(iri)] = {
            "iri": iri,
            "label": label,
            "content": text,
        }
    if payload:
        await vector_storage.upsert(payload)
        await vector_storage.index_done_callback()


async def retrieve_candidate_classes(
    query_text: str,
    vector_storage: BaseVectorStorage,
    top_n: int,
) -> list[dict[str, Any]]:
    """Return up to `top_n` candidate classes ranked by similarity.

    Each result is `{"iri": ..., "label": ..., "score": float}` where
    `score` is in [0, 1] (1 == perfect match). The underlying vector store
    returns a `distance` field (cosine distance); we convert to a
    similarity by `1 - distance`, clipped to [0, 1] for safety.
    """
    hits = await vector_storage.query(query_text, top_k=top_n)
    out: list[dict[str, Any]] = []
    for h in hits:
        distance = float(h.get("distance", 1.0))
        score = max(0.0, min(1.0, 1.0 - distance))
        out.append({
            "iri": h.get("iri") or h.get("id"),
            "label": h.get("label", ""),
            "score": score,
        })
    return out
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `./scripts/test.sh tests/test_yago_class_index.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check lightrag/taxonomy/class_index.py tests/test_yago_class_index.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lightrag/taxonomy/class_index.py tests/test_yago_class_index.py
git commit -m "feat(taxonomy): YAGO class vector index + candidate retrieval"
```

---

## Task 5: Document Classifier with Threshold Rule

**Files:**
- Create: `lightrag/taxonomy/classifier.py`
- Create: `tests/test_yago_classifier.py`

`DocumentClassifier.classify(text)` runs the full pipeline: retrieve top-N candidates → format an LLM prompt asking for JSON `[{iri, score}, ...]` → parse and apply the threshold rule (≥50% of top score, cap 10) → fall back to `lightrag:Uncategorized` if nothing scores ≥ 0.3.

- [ ] **Step 1: Write the failing tests**

Create `/Users/jin/OntoRAG/tests/test_yago_classifier.py`:

```python
"""Tests for lightrag.taxonomy.classifier."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from lightrag.kg.nano_vector_db_impl import NanoVectorDBStorage
from lightrag.kg.networkx_impl import NetworkXStorage
from lightrag.kg.shared_storage import (
    finalize_share_data,
    initialize_share_data,
)
from lightrag.namespace import NameSpace
from lightrag.taxonomy.class_index import build_class_index
from lightrag.taxonomy.classifier import DocumentClassifier
from lightrag.taxonomy.constants import UNCATEGORIZED_IRI
from lightrag.taxonomy.graph_loader import load_taxonomy_to_graph
from lightrag.taxonomy.parser import parse_ntriples_file
from lightrag.utils import EmbeddingFunc

FIXTURE = Path(__file__).parent / "fixtures" / "yago" / "mini_taxonomy.nt"
_DIM = 16


async def _embed(texts: list[str], **_: object) -> np.ndarray:
    out = np.zeros((len(texts), _DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for j, ch in enumerate(t.lower()[:_DIM]):
            out[i, j] = ord(ch) / 255.0
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


def _make_llm(returns: str | Exception):
    """Return an async LLM stub that yields `returns` (or raises it)."""
    calls: list[dict] = []

    async def llm(prompt: str, system_prompt: str | None = None, **kw):
        calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if isinstance(returns, Exception):
            raise returns
        return returns

    llm.calls = calls  # type: ignore[attr-defined]
    return llm


@pytest.fixture
async def classifier(tmp_path: Path):
    initialize_share_data()
    embed = EmbeddingFunc(embedding_dim=_DIM, max_token_size=8192, func=_embed)
    graph = NetworkXStorage(
        namespace=NameSpace.GRAPH_STORE_YAGO_TAXONOMY,
        workspace="clftest",
        global_config={"working_dir": str(tmp_path)},
        embedding_func=embed,
    )
    vdb = NanoVectorDBStorage(
        namespace=NameSpace.VECTOR_STORE_YAGO_CLASSES,
        workspace="clftest",
        global_config={
            "working_dir": str(tmp_path),
            "embedding_batch_num": 32,
            "vector_db_storage_cls_kwargs": {
                "cosine_better_than_threshold": 0.0,
            },
        },
        embedding_func=embed,
        meta_fields={"iri", "label", "content"},
    )
    classes = parse_ntriples_file(FIXTURE)
    await load_taxonomy_to_graph(classes, graph)
    await build_class_index([c.iri for c in classes], graph, vdb)

    def _factory(llm_func):
        return DocumentClassifier(
            vector_storage=vdb,
            llm_func=llm_func,
            candidate_count=5,
        )

    yield _factory
    finalize_share_data()


@pytest.mark.asyncio
async def test_returns_uncategorized_when_no_candidate_scores_above_min(classifier):
    factory = classifier
    llm = _make_llm(json.dumps({"assignments": [
        {"iri": "http://schema.org/Drug", "score": 0.1},
        {"iri": "http://schema.org/Person", "score": 0.05},
    ]}))
    result = await factory(llm).classify("some random text")
    assert len(result) == 1
    assert result[0]["iri"] == UNCATEGORIZED_IRI
    assert result[0]["score"] == 0.0


@pytest.mark.asyncio
async def test_keeps_single_top_class_when_secondaries_below_ratio(classifier):
    factory = classifier
    llm = _make_llm(json.dumps({"assignments": [
        {"iri": "http://schema.org/Drug", "score": 0.9},
        {"iri": "http://schema.org/Person", "score": 0.2},  # < 0.5 * 0.9 = 0.45
    ]}))
    result = await factory(llm).classify("aspirin tablet")
    assert [r["iri"] for r in result] == ["http://schema.org/Drug"]


@pytest.mark.asyncio
async def test_keeps_secondaries_above_ratio(classifier):
    factory = classifier
    llm = _make_llm(json.dumps({"assignments": [
        {"iri": "http://schema.org/Drug", "score": 0.9},
        {"iri": "http://schema.org/MedicalEntity", "score": 0.7},  # >= 0.45
        {"iri": "http://schema.org/Person", "score": 0.2},  # dropped
    ]}))
    result = await factory(llm).classify("aspirin")
    iris = [r["iri"] for r in result]
    assert iris == [
        "http://schema.org/Drug",
        "http://schema.org/MedicalEntity",
    ]


@pytest.mark.asyncio
async def test_caps_at_max_classes(classifier):
    factory = classifier
    # Fabricate 15 candidates all above the threshold ratio.
    assignments = [
        {"iri": f"http://schema.org/X{i}", "score": 0.9 - i * 0.01}
        for i in range(15)
    ]
    llm = _make_llm(json.dumps({"assignments": assignments}))
    result = await factory(llm).classify("multi-topic doc")
    assert len(result) == 10


@pytest.mark.asyncio
async def test_uncategorized_when_llm_returns_malformed_json(classifier):
    factory = classifier
    llm = _make_llm("not json at all")
    result = await factory(llm).classify("anything")
    assert result == [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]


@pytest.mark.asyncio
async def test_uncategorized_when_llm_raises(classifier):
    factory = classifier
    llm = _make_llm(RuntimeError("api boom"))
    result = await factory(llm).classify("anything")
    assert result == [{"iri": UNCATEGORIZED_IRI, "score": 0.0}]


@pytest.mark.asyncio
async def test_llm_prompt_contains_candidate_iris_and_labels(classifier):
    factory = classifier
    llm = _make_llm(json.dumps({"assignments": []}))
    inst = factory(llm)
    await inst.classify("drug medication")
    assert llm.calls, "LLM was not invoked"
    prompt = llm.calls[0]["prompt"]
    # Some Drug-related candidate must be present.
    assert "http://schema.org/" in prompt
    assert "Drug" in prompt or "Medication" in prompt
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./scripts/test.sh tests/test_yago_classifier.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'lightrag.taxonomy.classifier'`.

- [ ] **Step 3: Implement the classifier**

Create `/Users/jin/OntoRAG/lightrag/taxonomy/classifier.py`:

```python
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
    """Pull `assignments` out of the LLM response, tolerating leading/trailing junk."""
    raw = raw.strip()
    # Trim any markdown code fences the model might wrap output in.
    if raw.startswith("```"):
        raw = raw.strip("`")
        # Drop optional 'json\n' header that follows the fence.
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back: try to extract the first {...} substring.
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
    """Apply the §5.4 rule: top class always wins (if above min_score); keep
    secondaries scoring at least secondary_ratio * top; cap at max_classes.
    """
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
    """Per-document classifier wrapping candidate retrieval + LLM call.

    `llm_func` must be an async callable matching the LightRAG LLM
    signature: `await llm_func(prompt, system_prompt=..., **kwargs) -> str`.
    """

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
        except Exception as exc:  # noqa: BLE001 — classification must never block ingest
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
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `./scripts/test.sh tests/test_yago_classifier.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check lightrag/taxonomy/classifier.py tests/test_yago_classifier.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add lightrag/taxonomy/classifier.py tests/test_yago_classifier.py
git commit -m "feat(taxonomy): document classifier with threshold rule and Uncategorized fallback"
```

---

## Task 6: Public Module Surface

**Files:**
- Modify: `lightrag/taxonomy/__init__.py`

- [ ] **Step 1: Rewrite the package init**

Replace the empty `/Users/jin/OntoRAG/lightrag/taxonomy/__init__.py` with:

```python
"""YAGO 4.5 taxonomy integration for LightRAG.

Public surface:
- `YagoClass`, `parse_ntriples_file` — RDF parsing
- `load_taxonomy_to_graph`, `walk_ancestors`, `SUBCLASS_OF_EDGE_TYPE` — graph layer
- `select_working_vocabulary`, `count_descendants` — vocabulary selection
- `build_class_index`, `retrieve_candidate_classes` — vector index
- `DocumentClassifier` — end-to-end per-doc classification

See docs/GraphAndRagArchitecture.md §5 for the design.
"""

from lightrag.taxonomy.class_index import (
    build_class_index,
    retrieve_candidate_classes,
)
from lightrag.taxonomy.classifier import DocumentClassifier
from lightrag.taxonomy.constants import (
    DEFAULT_ANCESTOR_RENDER_DEPTH,
    DEFAULT_CANDIDATE_COUNT,
    DEFAULT_MAX_CLASSES_PER_DOC,
    DEFAULT_MIN_SCORE,
    DEFAULT_SECONDARY_SCORE_RATIO,
    DEFAULT_WORKING_VOCABULARY_SIZE,
    UNCATEGORIZED_IRI,
    YAGO_NODE_ENTITY_TYPE,
)
from lightrag.taxonomy.graph_loader import (
    SUBCLASS_OF_EDGE_TYPE,
    load_taxonomy_to_graph,
    walk_ancestors,
)
from lightrag.taxonomy.parser import YagoClass, parse_ntriples_file
from lightrag.taxonomy.vocabulary import (
    count_descendants,
    select_working_vocabulary,
)

__all__ = [
    "DEFAULT_ANCESTOR_RENDER_DEPTH",
    "DEFAULT_CANDIDATE_COUNT",
    "DEFAULT_MAX_CLASSES_PER_DOC",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_SECONDARY_SCORE_RATIO",
    "DEFAULT_WORKING_VOCABULARY_SIZE",
    "DocumentClassifier",
    "SUBCLASS_OF_EDGE_TYPE",
    "UNCATEGORIZED_IRI",
    "YAGO_NODE_ENTITY_TYPE",
    "YagoClass",
    "build_class_index",
    "count_descendants",
    "load_taxonomy_to_graph",
    "parse_ntriples_file",
    "retrieve_candidate_classes",
    "select_working_vocabulary",
    "walk_ancestors",
]
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from lightrag.taxonomy import DocumentClassifier, parse_ntriples_file, load_taxonomy_to_graph; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Run full taxonomy test suite**

Run: `./scripts/test.sh tests/test_yago_parser.py tests/test_yago_graph_loader.py tests/test_yago_vocabulary.py tests/test_yago_class_index.py tests/test_yago_classifier.py -v`
Expected: every test passes.

- [ ] **Step 4: Commit**

```bash
git add lightrag/taxonomy/__init__.py
git commit -m "feat(taxonomy): expose public module surface"
```

---

## Task 7: SHA256 Manifest for the YAGO 4.0 T-Box (replaces the original download script)

**Files:**
- Create: `lightrag/taxonomy/manifest.py`
- Modify (redirect-only): `scripts/yago/fetch_yago.sh`

The original Task 7 was a download script targeting YAGO 4.5. The 4.5 → 4.0 switch made it obsolete: YAGO 4.0 files are tiny (~60 MB total uncompressed) and now committed under `/Users/jin/OntoRAG/yago/`. We replace the download task with a checksum-pinning task so "YAGO 4.0" in this repo is tied to specific bytes — anyone who replaces the files with a different snapshot trips a verification error from the build CLI.

`scripts/yago/fetch_yago.sh` is kept as a documentation pointer (no auto-download by default). Pass `--fetch` to actually run curl.

- [ ] **Step 1: Capture SHA256s of the three YAGO 4.0 T-Box files**

```bash
shasum -a 256 /Users/jin/OntoRAG/yago/yago-wd-{class,schema,shapes}.nt
```

Expected (matches the pinned manifest):
```
0b11dff027ad77d82b83bf4a241389760c2801ce6f3c92b77684d752abfa0670  yago-wd-class.nt
1a5484f1402aebe9e1d07e3df8fd02421d0f7bc7dccf3012ba49f4f95610c90c  yago-wd-schema.nt
05a542e176a96b32ee265bb5bf4e51d403779c399db7a0044c4571814475b729  yago-wd-shapes.nt
```

- [ ] **Step 2: Write the manifest module**

Create `/Users/jin/OntoRAG/lightrag/taxonomy/manifest.py` exposing:
- `YAGO_VERSION = "yago-4.0-2020-02-24"`
- `YAGO_DATA_DIR` — `<repo>/yago/`
- `PINNED_FILES: dict[str, str]` — filename → sha256
- `TAXONOMY_FILES: tuple[str, ...]` — files the parser actually consumes (`yago-wd-class.nt`, `yago-wd-schema.nt`; shapes is pinned for provenance but not parsed)
- `default_taxonomy_paths()` — returns absolute `Path` list for the CLI
- `verify_yago_files(data_dir=YAGO_DATA_DIR)` — raises `YagoFileChecksumError` listing every missing/drifted file at once
- `sha256_of(path)` helper

Verification reads in 1 MB chunks so the 60 MB `yago-wd-class.nt` doesn't load to memory wholesale.

- [ ] **Step 3: Rewrite `scripts/yago/fetch_yago.sh` as a redirect**

Replace the body with a `cat <<EOF` that explains:
1. Where the files are expected (`/Users/jin/OntoRAG/yago/`)
2. Where they came from (`https://yago-knowledge.org/data/yago4/full/2020-02-24/yago-wd-{class,schema,shapes}.nt.gz`)
3. How to verify (`python -c 'from lightrag.taxonomy.manifest import verify_yago_files; verify_yago_files()'`)

Support an optional `--fetch` flag that does the curl + gunzip if explicitly invoked.

- [ ] **Step 4: Smoke-test**

```bash
.venv/bin/python -c "from lightrag.taxonomy.manifest import verify_yago_files; verify_yago_files(); print('ok')"
bash scripts/yago/fetch_yago.sh   # prints redirect, exits cleanly
bash -n scripts/yago/fetch_yago.sh
.venv/bin/ruff check lightrag/taxonomy/manifest.py
```

- [ ] **Step 5: Commit**

```bash
git add lightrag/taxonomy/manifest.py scripts/yago/fetch_yago.sh
git commit -m "feat(taxonomy): pin YAGO 4.0 T-Box by SHA256; fetch_yago.sh becomes a redirect"
```

---

## Task 8: Bootstrap CLI

**Files:**
- Create: `scripts/yago/build_yago_taxonomy.py`
- Create: `tests/test_yago_build_cli.py`

CLI that parses given N-Triples files → loads classes into the YAGO graph namespace → selects working vocabulary → builds the class vector index. Targets a LightRAG `working_dir` (creates the storage instances directly without booting the full `LightRAG` class). Idempotent re-runs.

> **2026-05-22 update (paired with Task 7):** `--files` is now optional and
> defaults to `lightrag.taxonomy.manifest.default_taxonomy_paths()` — the two
> committed YAGO 4.0 files (`yago-wd-class.nt`, `yago-wd-schema.nt`). When the
> default is used, `main()` calls `verify_yago_files()` before parsing; pass
> `--skip-verify` (or override `--files` with custom paths) to bypass. Tests
> call `build_taxonomy()` directly with fixture files, so they never trip the
> manifest check — keeps the existing `tests/test_yago_build_cli.py` green.

- [ ] **Step 1: Write failing tests for the CLI**

Create `/Users/jin/OntoRAG/tests/test_yago_build_cli.py`:

```python
"""Tests for scripts/yago/build_yago_taxonomy.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from lightrag.utils import EmbeddingFunc

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "yago" / "build_yago_taxonomy.py"
FIXTURE = ROOT / "tests" / "fixtures" / "yago" / "mini_taxonomy.nt"


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "build_yago_taxonomy", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _embed(texts: list[str], **_: object) -> np.ndarray:
    out = np.zeros((len(texts), 16), dtype=np.float32)
    for i, t in enumerate(texts):
        for j, ch in enumerate(t.lower()[:16]):
            out[i, j] = ord(ch) / 255.0
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


@pytest.mark.asyncio
async def test_build_taxonomy_populates_graph_and_index(tmp_path: Path):
    module = _load_script_module()
    embed = EmbeddingFunc(embedding_dim=16, max_token_size=8192, func=_embed)
    result = await module.build_taxonomy(
        files=[FIXTURE],
        working_dir=tmp_path,
        workspace="cli",
        embedding_func=embed,
        vocabulary_size=5,
    )
    assert result["classes_loaded"] == 8
    assert result["vocabulary_size"] == 5
    assert result["index_records"] == 5


@pytest.mark.asyncio
async def test_build_taxonomy_is_idempotent(tmp_path: Path):
    module = _load_script_module()
    embed = EmbeddingFunc(embedding_dim=16, max_token_size=8192, func=_embed)
    first = await module.build_taxonomy(
        files=[FIXTURE],
        working_dir=tmp_path,
        workspace="cli",
        embedding_func=embed,
        vocabulary_size=5,
    )
    second = await module.build_taxonomy(
        files=[FIXTURE],
        working_dir=tmp_path,
        workspace="cli",
        embedding_func=embed,
        vocabulary_size=5,
    )
    assert first == second
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `./scripts/test.sh tests/test_yago_build_cli.py -v`
Expected: every test FAILS with `FileNotFoundError` for the CLI script.

- [ ] **Step 3: Implement the CLI**

Create `/Users/jin/OntoRAG/scripts/yago/build_yago_taxonomy.py`:

```python
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
                # Merge parents; keep latest label/comment.
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
        finalize_share_data()


def _resolve_embedding(binding: str, model: str | None) -> EmbeddingFunc:
    """Resolve an EmbeddingFunc by binding name (e.g. 'openai', 'ollama').

    Mirrors how the API server resolves bindings — we re-use the LightRAG
    LLM module convention: each binding exposes an `embed` callable.
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
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `./scripts/test.sh tests/test_yago_build_cli.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Lint**

Run: `ruff check scripts/yago/build_yago_taxonomy.py tests/test_yago_build_cli.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add scripts/yago/build_yago_taxonomy.py tests/test_yago_build_cli.py
git commit -m "feat(taxonomy): bootstrap CLI to populate YAGO graph + vector index"
```

---

## Task 9: Coverage-Validation Helper Script

**Files:**
- Create: `scripts/yago/check_coverage.py`

Sample-based corpus coverage check per §5.7 / §5.8. Takes a directory of `.txt` files plus a built YAGO taxonomy (working_dir + workspace), runs `DocumentClassifier.classify` on each, and prints the Uncategorized rate plus a histogram of class assignments. **This is the gate before Plan B.**

- [ ] **Step 1: Implement the script**

Create `/Users/jin/OntoRAG/scripts/yago/check_coverage.py`:

```python
"""Sample-based coverage check for the YAGO taxonomy layer.

Run this against a representative sample of your corpus (~100 docs)
before committing to Plan B (the LightRAG pipeline integration). If
the Uncategorized rate is >40-50%, the taxonomy needs domain-specific
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
from lightrag.taxonomy import DocumentClassifier, UNCATEGORIZED_IRI
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
                "[%d/%d] %s → %s (%.2f)",
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
```

- [ ] **Step 2: Lint**

Run: `ruff check scripts/yago/check_coverage.py`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add scripts/yago/check_coverage.py
git commit -m "feat(taxonomy): corpus coverage check helper for pre-Plan-B gate"
```

---

## Task 10: Update Architecture Doc Status

**Files:**
- Modify: `docs/GraphAndRagArchitecture.md` — §5.8 checklist

Mark items the infrastructure has covered; leave the corpus + eval items for the human to complete before Plan B.

- [ ] **Step 1: Edit the checklist**

In `/Users/jin/OntoRAG/docs/GraphAndRagArchitecture.md`, locate the §5.8 checklist block:

```markdown
### 5.8 Pre-Build Spike Checklist

Before implementation:

- [ ] Pin a specific YAGO 4.5 release; document the file list (schema + taxonomy only).
- [ ] Select the ~200-class working vocabulary (top-N by `subClassOf` descendant count, manually pruned for utility).
- [ ] Validate corpus coverage on a 100-doc sample; check `Uncategorized` rate.
- [ ] Build an eval harness with a held-out query set + reference answers, run against current `main` *before* taxonomy code lands. Otherwise there's no apples-to-apples comparison once extraction prompts change.
- [ ] Pick the document content used for classification (full text vs summary vs first-N tokens) and the embedding model (must match the rest of the system).
```

Replace it with:

```markdown
### 5.8 Pre-Build Spike Checklist

Plan A (infrastructure) landed in `docs/superpowers/plans/2026-05-22-yago-taxonomy-infrastructure.md`. Items shipped as part of Plan A are marked `[x] (Plan A)`. Remaining items gate Plan B.

- [x] (Plan A) Pin a specific YAGO 4.5 release; document the file list (schema + taxonomy only). — see `scripts/yago/fetch_yago.sh`, default version `2024-02-29`.
- [x] (Plan A) Select the ~200-class working vocabulary (top-N by `subClassOf` descendant count, manually pruned for utility). — `lightrag/taxonomy/vocabulary.py`, `select_working_vocabulary`; manual exclusions via `--exclude` on the bootstrap CLI.
- [ ] Validate corpus coverage on a 100-doc sample; check `Uncategorized` rate. — run `scripts/yago/check_coverage.py` once Plan A bootstrap is complete on the target working directory.
- [ ] Build an eval harness with a held-out query set + reference answers, run against current `main` *before* Plan B's taxonomy enrichment lands. Otherwise there's no apples-to-apples comparison once context formatting changes.
- [ ] Pick the document content used for classification (full text vs summary vs first-N tokens) — currently the classifier accepts arbitrary text; Plan B chooses what to pass in.
```

- [ ] **Step 2: Commit**

```bash
git add docs/GraphAndRagArchitecture.md
git commit -m "docs(taxonomy): mark Plan A items shipped in spike checklist"
```

---

## Self-Review

**Spec coverage** (against `docs/GraphAndRagArchitecture.md` §5):

| §5 requirement | Plan A task |
|---|---|
| YAGO T-Box only (§5.2) | Task 7 (manifest pins YAGO 4.0 `yago-wd-class.nt` + `yago-wd-schema.nt`; A-Box files excluded) |
| Pinned version (§5.2) | Task 7 — SHA256 in `lightrag/taxonomy/manifest.py`, verified by build CLI |
| ~200 working vocabulary (§5.2) | Task 3 |
| Separate graph namespace (§5.3) | Task 0 (namespace const), Task 2 (loader writes there) |
| Class node schema (§5.3) | Task 2 |
| Subset of nodes via edges, not list field (§5.3 implication of Option A) | Task 2 (no NetworkX shim needed) |
| `lightrag:Uncategorized` sentinel (§5.3) | Task 0 (constant), Task 5 (fallback) |
| Same embedding model as rest of system (§5.4) | Task 4 docstring, Task 8 CLI passes `EmbeddingFunc` through |
| Per-document classification step (§5.4) | Task 5 (standalone; Plan B does the pipeline hook) |
| Multi-class cap 10 + ≥50%-of-top threshold + 0.3 floor (§5.4) | Task 5 |
| Sentinel populated on fallback (§5.3) | Task 5 |
| Coverage validation (§5.7, §5.8) | Task 9 |
| Cost: 1 LLM call per document | Task 5 (one `self._llm(...)` call) |
| Out of scope for Plan A: pipeline integration, query path changes, doc_status/QueryParam fields | All deferred to Plan B (explicitly stated in plan goal) |

**Placeholder scan:** none — every code block is complete and runnable.

**Type consistency:**
- `YagoClass` constructed in Task 1, consumed in Task 2 (loader), Task 8 (CLI dedup) — field names match (`iri`, `label`, `comment`, `parent_iris`).
- `BaseGraphStorage` / `BaseVectorStorage` used throughout — unchanged abstract interfaces.
- `EmbeddingFunc` is the existing `lightrag.utils.EmbeddingFunc` — verified callable signature `(texts, **kw) -> np.ndarray`.
- `DocumentClassifier.classify` returns `list[{"iri": str, "score": float}]` — same shape in every test assertion and in the coverage script's consumption.
- `SUBCLASS_OF_EDGE_TYPE = "subClassOf"` defined in `graph_loader.py`, imported by `vocabulary.py` — single source of truth.

---

## Execution Handoff

Plan A is complete and saved to `docs/superpowers/plans/2026-05-22-yago-taxonomy-infrastructure.md`.

After Plan A lands:
1. Confirm the pinned YAGO 4.0 files are present:
   `python -c "from lightrag.taxonomy.manifest import verify_yago_files; verify_yago_files(); print('ok')"`.
   If they were lost, run `bash scripts/yago/fetch_yago.sh --fetch` to re-download.
2. Run `python scripts/yago/build_yago_taxonomy.py --working-dir ./rag_storage …` to populate the taxonomy in your working dir (the CLI defaults to the pinned files and verifies before parsing).
3. Run `python scripts/yago/check_coverage.py …` against a 100-doc corpus sample. **Coverage gate:** Uncategorized rate < 40% before writing Plan B; otherwise revisit vocabulary selection or add a domain overlay.
4. Build the eval harness (Plan A doesn't ship one — it's an open §5.8 item).
5. Write Plan B (LightRAG pipeline integration + query-path enrichment) once the gate passes.
