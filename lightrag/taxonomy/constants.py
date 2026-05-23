"""Constants for the YAGO 4.5 taxonomy integration.

Centralizes RDF prefixes, namespace identifiers, sentinel IRIs, and tunable
thresholds so every taxonomy module shares one source of truth.
"""

from __future__ import annotations

RDFS_SUBCLASS_OF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"

YAGO_NODE_ENTITY_TYPE = "YagoClass"

UNCATEGORIZED_IRI = "lightrag:Uncategorized"

DEFAULT_MAX_CLASSES_PER_DOC = 10
DEFAULT_SECONDARY_SCORE_RATIO = 0.5
DEFAULT_MIN_SCORE = 0.3

DEFAULT_ANCESTOR_RENDER_DEPTH = 3

DEFAULT_WORKING_VOCABULARY_SIZE = 200

DEFAULT_CANDIDATE_COUNT = 20

LABEL_LANGUAGE = "en"
