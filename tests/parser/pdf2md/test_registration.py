import pytest

from ontorag.constants import PARSER_ENGINE_PDF2MD
from ontorag.parser import registry

pytestmark = pytest.mark.offline


def test_pdf2md_spec_registered():
    spec = registry.parser_specs_snapshot()[PARSER_ENGINE_PDF2MD]
    assert spec.impl == "ontorag.parser.pdf2md.parser:Pdf2MdParser"
    assert spec.suffixes == frozenset({"pdf", "epub", "docx", "doc", "odt", "rtf"})
    assert spec.queue_group == "pdf2md"
    assert spec.concurrency == 2
    # Available (pymupdf installed) -> no requirement; unavailable -> hint.
    from ontorag.parser.pdf2md import probe

    assert spec.endpoint_requirement() in (None, probe.INSTALL_HINT)
    assert probe.INSTALL_HINT == "pip install 'ontorag[pdf2md]'"


def test_pdf2md_unavailable_falls_through_routing(monkeypatch):
    from ontorag.parser.pdf2md import probe
    from ontorag.parser.routing import resolve_file_parser_engine

    monkeypatch.setattr(probe, "check_pdf2md_available", lambda: False)
    monkeypatch.setenv("ONTORAG_PARSER", "pdf:pdf2md,*:legacy")
    assert resolve_file_parser_engine("book.pdf") == "legacy"


def test_pdf2md_available_claims_pdf(monkeypatch):
    from ontorag.parser.pdf2md import probe
    from ontorag.parser.routing import resolve_file_parser_engine

    monkeypatch.setattr(probe, "check_pdf2md_available", lambda: True)
    monkeypatch.setenv("ONTORAG_PARSER", "pdf:pdf2md,*:legacy")
    assert resolve_file_parser_engine("book.pdf") == "pdf2md"
