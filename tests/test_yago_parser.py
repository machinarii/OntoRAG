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
