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
