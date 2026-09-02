"""Test-only converter engine: parses like legacy but re-points the source.

Registered by tests/pipeline/test_canonical_source.py under the engine name
``fakeconv``. It writes a stand-in ``<stem>.textpack`` beside the source,
persists the body, archives the bundle (as the real Markdown delegate does)
and returns a ParseResult carrying ``canonical_source`` / ``document_metadata``.
"""

from __future__ import annotations

from ontorag.constants import FULL_DOCS_FORMAT_RAW
from ontorag.parser.base import BaseParser, ParseContext, ParseResult


class FakeConvParser(BaseParser):
    engine_name = "fakeconv"

    async def parse(self, ctx: ParseContext) -> ParseResult:
        source = ctx.source_path(self.engine_name)
        body = source.read_text(encoding="utf-8")
        bundle = source.with_suffix(".textpack")
        bundle.write_text(body, encoding="utf-8")  # stands in for the real bundle
        await ctx.rag._persist_parsed_full_docs(
            ctx.doc_id, {"content": body, "parse_format": FULL_DOCS_FORMAT_RAW}
        )
        await ctx.archive_source(str(bundle))
        return ParseResult(
            doc_id=ctx.doc_id,
            file_path=ctx.file_path,
            parse_format=FULL_DOCS_FORMAT_RAW,
            content=body,
            parse_engine=self.engine_name,
            canonical_source=bundle.name,
            document_metadata={
                "doc_type": "document",
                "bibliographic": {"title": "T"},
            },
        )
