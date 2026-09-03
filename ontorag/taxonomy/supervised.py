"""A supervised class prior that learns from confirmed assignments.

paperless-ngx retrains a scikit-learn classifier on user-confirmed labels so
its LLM suggestions get cheaper and more consistent over time (design
borrowed, no code). This is the numpy-only equivalent: a multinomial naive
Bayes over unigram + bigram counts, fitted on ``(text, assignments)``
examples a human (or an accepted LLM run) confirmed, JSON-persisted with a
training fingerprint so callers retrain only when the confirmed set changed.

``DocumentClassifier`` uses it through the ``ClassPrior`` protocol: the
probabilities re-rank candidates, and a confident top class skips the LLM.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


def tokenize(text: str) -> list[str]:
    """Lower-cased word unigrams plus adjacent-word bigrams."""
    words = _TOKEN_RE.findall((text or "").lower())
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


class NaiveBayesClassPrior:
    alpha = 1.0  # Laplace smoothing

    def __init__(self) -> None:
        self.labels: dict[str, str] = {}
        self.example_counts: dict[str, int] = {}
        self.fingerprint: str | None = None
        self._token_counts: dict[str, Counter[str]] = {}
        self._total_tokens: dict[str, int] = {}
        self._vocab: set[str] = set()

    # ---- training ---------------------------------------------------------
    def fit(
        self,
        examples: Iterable[tuple[str, list[dict[str, Any]]]],
        *,
        fingerprint: str | None = None,
    ) -> "NaiveBayesClassPrior":
        self.labels, self.example_counts = {}, {}
        self._token_counts, self._total_tokens, self._vocab = {}, {}, set()
        for text, assignments in examples:
            tokens = tokenize(text)
            if not tokens:
                continue
            counts = Counter(tokens)
            for a in assignments or []:
                iri = a.get("iri") if isinstance(a, dict) else None
                if not isinstance(iri, str) or not iri:
                    continue
                self.example_counts[iri] = self.example_counts.get(iri, 0) + 1
                if a.get("label") and not self.labels.get(iri):
                    self.labels[iri] = str(a["label"])
                self.labels.setdefault(iri, "")
                bucket = self._token_counts.setdefault(iri, Counter())
                bucket.update(counts)
                self._total_tokens[iri] = self._total_tokens.get(iri, 0) + len(tokens)
            self._vocab.update(counts)
        self.fingerprint = fingerprint
        return self

    def is_stale(self, fingerprint: str | None) -> bool:
        return fingerprint != self.fingerprint

    # ---- inference --------------------------------------------------------
    def predict_proba(self, text: str) -> dict[str, float]:
        classes = list(self.example_counts)
        if not classes:
            return {}
        total_examples = sum(self.example_counts.values())
        vocab_size = len(self._vocab) or 1
        tokens = [t for t in tokenize(text) if t in self._vocab]
        counts = Counter(tokens)
        log_probs = np.empty(len(classes), dtype=np.float64)
        for i, iri in enumerate(classes):
            lp = math.log(self.example_counts[iri] / total_examples)
            denom = self._total_tokens.get(iri, 0) + self.alpha * vocab_size
            bucket = self._token_counts.get(iri, Counter())
            for tok, n in counts.items():
                lp += n * math.log((bucket.get(tok, 0) + self.alpha) / denom)
            log_probs[i] = lp
        log_probs -= log_probs.max()
        probs = np.exp(log_probs)
        probs /= probs.sum()
        return {iri: float(p) for iri, p in zip(classes, probs)}

    def top(self, text: str, k: int = 3) -> list[tuple[str, float]]:
        probs = self.predict_proba(text)
        return sorted(probs.items(), key=lambda kv: -kv[1])[:k]

    # ---- persistence ------------------------------------------------------
    def save(self, path: Path) -> Path:
        payload = {
            "schema": 1,
            "fingerprint": self.fingerprint,
            "labels": self.labels,
            "example_counts": self.example_counts,
            "token_counts": {iri: dict(c) for iri, c in self._token_counts.items()},
            "total_tokens": self._total_tokens,
            "vocab": sorted(self._vocab),
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "NaiveBayesClassPrior":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        prior = cls()
        prior.fingerprint = payload.get("fingerprint")
        prior.labels = dict(payload.get("labels", {}))
        prior.example_counts = {
            k: int(v) for k, v in payload.get("example_counts", {}).items()
        }
        prior._token_counts = {
            iri: Counter({t: int(n) for t, n in c.items()})
            for iri, c in payload.get("token_counts", {}).items()
        }
        prior._total_tokens = {
            k: int(v) for k, v in payload.get("total_tokens", {}).items()
        }
        prior._vocab = set(payload.get("vocab", []))
        return prior
