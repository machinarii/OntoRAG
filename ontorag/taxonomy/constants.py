"""Constants for the YAGO 4.5 taxonomy integration.

Centralizes RDF prefixes, namespace identifiers, sentinel IRIs, and tunable
thresholds so every taxonomy module shares one source of truth.
"""

from __future__ import annotations

RDFS_SUBCLASS_OF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"

YAGO_NODE_ENTITY_TYPE = "YagoClass"

UNCATEGORIZED_IRI = "ontorag:Uncategorized"

DEFAULT_MAX_CLASSES_PER_DOC = 10
DEFAULT_SECONDARY_SCORE_RATIO = 0.5
DEFAULT_MIN_SCORE = 0.3

DEFAULT_ANCESTOR_RENDER_DEPTH = 3

DEFAULT_WORKING_VOCABULARY_SIZE = 200

DEFAULT_CANDIDATE_COUNT = 20

# Neighbour-label candidates (paperless-ngx lesson): how many similar
# already-classified documents vote, and how their votes weigh against the
# class-index similarity when merged.
DEFAULT_NEIGHBOR_TOP_K = 15
DEFAULT_NEIGHBOR_WEIGHT = 1.0

# Free-form names the LLM suggests are reconciled to candidate labels with
# difflib at this cutoff (one-to-one); the rest surface as ``unmatched_names``.
DEFAULT_NAME_MATCH_CUTOFF = 0.8

# A supervised prior confident above this probability skips the LLM call.
DEFAULT_PRIOR_SKIP_THRESHOLD = 0.9

LABEL_LANGUAGE = "en"
