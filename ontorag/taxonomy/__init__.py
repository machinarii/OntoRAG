"""YAGO 4.5 taxonomy integration for OntoRAG.

Public surface:
- `YagoClass`, `parse_ntriples_file` — RDF parsing
- `load_taxonomy_to_graph`, `walk_ancestors`, `SUBCLASS_OF_EDGE_TYPE` — graph layer
- `select_working_vocabulary`, `count_descendants` — vocabulary selection
- `build_class_index`, `retrieve_candidate_classes` — vector index
- `DocumentClassifier`, `ClassificationResult` — end-to-end per-doc classification
- `neighbor_class_candidates`, `merge_candidates` — neighbour-label candidates
- `NaiveBayesClassPrior` — supervised prior learned from confirmed assignments

See docs/GraphAndRagArchitecture.md §5 for the design.
"""

from ontorag.taxonomy.class_index import (
    build_class_index,
    retrieve_candidate_classes,
)
from ontorag.taxonomy.classifier import ClassificationResult, DocumentClassifier
from ontorag.taxonomy.constants import (
    DEFAULT_ANCESTOR_RENDER_DEPTH,
    DEFAULT_CANDIDATE_COUNT,
    DEFAULT_MAX_CLASSES_PER_DOC,
    DEFAULT_MIN_SCORE,
    DEFAULT_NAME_MATCH_CUTOFF,
    DEFAULT_NEIGHBOR_TOP_K,
    DEFAULT_NEIGHBOR_WEIGHT,
    DEFAULT_PRIOR_SKIP_THRESHOLD,
    DEFAULT_SECONDARY_SCORE_RATIO,
    DEFAULT_WORKING_VOCABULARY_SIZE,
    UNCATEGORIZED_IRI,
    YAGO_NODE_ENTITY_TYPE,
)
from ontorag.taxonomy.graph_loader import (
    SUBCLASS_OF_EDGE_TYPE,
    load_taxonomy_to_graph,
    walk_ancestors,
)
from ontorag.taxonomy.neighbors import merge_candidates, neighbor_class_candidates
from ontorag.taxonomy.parser import YagoClass, parse_ntriples_file
from ontorag.taxonomy.supervised import NaiveBayesClassPrior
from ontorag.taxonomy.vocabulary import (
    count_descendants,
    select_working_vocabulary,
)

__all__ = [
    "DEFAULT_ANCESTOR_RENDER_DEPTH",
    "DEFAULT_CANDIDATE_COUNT",
    "DEFAULT_MAX_CLASSES_PER_DOC",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_NAME_MATCH_CUTOFF",
    "DEFAULT_NEIGHBOR_TOP_K",
    "DEFAULT_NEIGHBOR_WEIGHT",
    "DEFAULT_PRIOR_SKIP_THRESHOLD",
    "DEFAULT_SECONDARY_SCORE_RATIO",
    "DEFAULT_WORKING_VOCABULARY_SIZE",
    "ClassificationResult",
    "DocumentClassifier",
    "NaiveBayesClassPrior",
    "SUBCLASS_OF_EDGE_TYPE",
    "UNCATEGORIZED_IRI",
    "YAGO_NODE_ENTITY_TYPE",
    "YagoClass",
    "build_class_index",
    "count_descendants",
    "load_taxonomy_to_graph",
    "merge_candidates",
    "neighbor_class_candidates",
    "parse_ntriples_file",
    "retrieve_candidate_classes",
    "select_working_vocabulary",
    "walk_ancestors",
]
