"""The 5x5 confusion matrix and per-state precision/recall. SPEC 9.

"Precision 98%" with no defined population is meaningless, so every number here is
reported against a stated denominator: the recon units scored, after the same-axis
compound classes are excluded.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from afc.config import STATES


@dataclass(frozen=True)
class StateScore:
    state: str
    support: int          # true instances
    predicted: int
    true_positives: int

    @property
    def precision(self) -> float:
        return self.true_positives / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass(frozen=True)
class ConfusionMatrix:
    counts: dict[tuple[str, str], int]     # (true, predicted) -> n
    scored: int

    @property
    def correct(self) -> int:
        return sum(n for (t, p), n in self.counts.items() if t == p)

    @property
    def accuracy(self) -> float:
        return self.correct / self.scored if self.scored else 0.0

    def per_state(self) -> dict[str, StateScore]:
        out = {}
        for state in STATES:
            support = sum(n for (t, _), n in self.counts.items() if t == state)
            predicted = sum(n for (_, p), n in self.counts.items() if p == state)
            tp = self.counts.get((state, state), 0)
            out[state] = StateScore(state, support, predicted, tp)
        return out

    def rows(self) -> list[list[int]]:
        return [[self.counts.get((t, p), 0) for p in STATES] for t in STATES]

    def as_dict(self) -> dict[str, object]:
        per = self.per_state()
        return {
            "labels": list(STATES),
            "matrix_true_by_predicted": self.rows(),
            "scored": self.scored,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 6),
            "per_state": {
                s: {
                    "support": v.support, "predicted": v.predicted,
                    "true_positives": v.true_positives,
                    "precision": round(v.precision, 6),
                    "recall": round(v.recall, 6), "f1": round(v.f1, 6),
                }
                for s, v in per.items()
            },
        }


def build(pairs: list[tuple[str, str]]) -> ConfusionMatrix:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for true_state, predicted in pairs:
        counts[(true_state, predicted)] += 1
    return ConfusionMatrix(dict(counts), len(pairs))
