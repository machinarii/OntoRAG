"""OntoRAG Sidecar writer infrastructure.

Spec: ``docs/OntoRAGSidecarFormat-zh.md``.

This package owns the *single executable specification* of the OntoRAG Sidecar
file format. Parser engines (native / mineru / docling) hand it an
``IRDoc`` (intermediate representation) describing the document; the writer
emits the spec-compliant ``*.parsed/`` directory.

See :func:`ontorag.sidecar.writer.write_sidecar` for the entry point.
"""

from ontorag.sidecar.ir import (
    AssetSpec,
    IRBlock,
    IRDoc,
    IRDrawing,
    IREquation,
    IRPosition,
    IRTable,
)
from ontorag.sidecar.writer import write_sidecar

__all__ = [
    "AssetSpec",
    "IRBlock",
    "IRDoc",
    "IRDrawing",
    "IREquation",
    "IRPosition",
    "IRTable",
    "write_sidecar",
]
