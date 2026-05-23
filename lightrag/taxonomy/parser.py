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


_IRI_TRIPLE = re.compile(r"^<([^>]+)>\s+<([^>]+)>\s+<([^>]+)>\s*\.\s*$")
_LIT_TRIPLE = re.compile(
    r'^<([^>]+)>\s+<([^>]+)>\s+"((?:\\.|[^"\\])*)"@(\w+)\s*\.\s*$'
)


def _unescape_literal(raw: str) -> str:
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
