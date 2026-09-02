import pytest

from ontorag.parser.base import ParseResult

pytestmark = pytest.mark.offline


def test_to_dict_omits_new_fields_when_unset():
    r = ParseResult(doc_id="d", file_path="a.pdf", parse_format="raw", content="x")
    assert "canonical_source" not in r.to_dict()
    assert "document_metadata" not in r.to_dict()


def test_to_dict_emits_new_fields_when_set():
    r = ParseResult(
        doc_id="d",
        file_path="a.pdf",
        parse_format="raw",
        content="x",
        canonical_source="a.textpack",
        document_metadata={"doc_type": "book"},
    )
    d = r.to_dict()
    assert d["canonical_source"] == "a.textpack"
    assert d["document_metadata"] == {"doc_type": "book"}
