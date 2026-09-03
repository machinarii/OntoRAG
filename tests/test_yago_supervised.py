"""Tests for ontorag.taxonomy.supervised — a numpy-only naive Bayes prior
fitted on confirmed class assignments (paperless-ngx's learn-from-corrections
loop, without the scikit-learn dependency)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ontorag.taxonomy.supervised import NaiveBayesClassPrior, tokenize

pytestmark = pytest.mark.offline

DRUG = "http://schema.org/Drug"
PERSON = "http://schema.org/Person"

EXAMPLES = [
    (
        "aspirin tablets relieve headache pain; take one tablet with water",
        [{"iri": DRUG, "label": "Drug"}],
    ),
    (
        "ibuprofen is an anti-inflammatory drug sold as tablets",
        [{"iri": DRUG, "label": "Drug"}],
    ),
    (
        "paracetamol dosage for adults: two tablets every six hours",
        [{"iri": DRUG, "label": "Drug"}],
    ),
    (
        "Marie Curie was a physicist and chemist born in Warsaw",
        [{"iri": PERSON, "label": "Person"}],
    ),
    (
        "Alan Turing, mathematician, was born in London in 1912",
        [{"iri": PERSON, "label": "Person"}],
    ),
    (
        "the biography of Ada Lovelace, born in 1815",
        [{"iri": PERSON, "label": "Person"}],
    ),
]


def test_tokenize_unigrams_and_bigrams():
    toks = tokenize("Aspirin Tablets, aspirin!")
    assert "aspirin" in toks and "tablets" in toks
    assert "aspirin tablets" in toks and "tablets aspirin" in toks
    assert "," not in toks


def test_fit_predicts_the_right_class():
    prior = NaiveBayesClassPrior().fit(EXAMPLES, fingerprint="v1")
    drug = prior.predict_proba("take two tablets of this drug for pain")
    person = prior.predict_proba("she was born in 1867 and became a chemist")
    assert max(drug, key=drug.get) == DRUG
    assert max(person, key=person.get) == PERSON
    assert abs(sum(drug.values()) - 1.0) < 1e-9
    assert prior.labels == {DRUG: "Drug", PERSON: "Person"}


def test_unknown_words_do_not_crash_and_empty_model_predicts_nothing():
    assert NaiveBayesClassPrior().predict_proba("anything") == {}
    prior = NaiveBayesClassPrior().fit(EXAMPLES, fingerprint="v1")
    out = prior.predict_proba("zxq qwv")
    assert set(out) == {DRUG, PERSON}


def test_top_returns_sorted_pairs():
    prior = NaiveBayesClassPrior().fit(EXAMPLES, fingerprint="v1")
    top = prior.top("aspirin tablets", k=1)
    assert len(top) == 1 and top[0][0] == DRUG and 0 < top[0][1] <= 1


def test_save_load_round_trip_and_staleness(tmp_path: Path):
    prior = NaiveBayesClassPrior().fit(EXAMPLES, fingerprint="abc")
    path = tmp_path / "prior.json"
    prior.save(path)
    loaded = NaiveBayesClassPrior.load(path)
    assert loaded.fingerprint == "abc"
    assert loaded.labels == prior.labels
    assert loaded.predict_proba("aspirin tablets") == pytest.approx(
        prior.predict_proba("aspirin tablets")
    )
    assert loaded.is_stale("abc") is False
    assert loaded.is_stale("def") is True


def test_multi_label_examples_count_for_every_class():
    examples = EXAMPLES + [
        (
            "a doctor prescribes drugs",
            [{"iri": DRUG, "label": "Drug"}, {"iri": PERSON, "label": "Person"}],
        )
    ]
    prior = NaiveBayesClassPrior().fit(examples, fingerprint="v2")
    assert prior.example_counts[DRUG] == 4 and prior.example_counts[PERSON] == 4
