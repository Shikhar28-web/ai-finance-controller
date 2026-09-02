"""Scoring the decomposer, not just the state machine. SPEC 9 addition B.

The 5x5 confusion matrix scores WHICH STATE a record landed in. It says nothing about
whether the decomposer named the right causes or attributed the right rupees -- which
is the claim SPEC 1 actually makes: "explain why two numbers differ, by naming the
specific deduction that caused it". Two metrics, both against settlement-grain ground
truth recorded at injection time:

    exact_cause_set_match_rate   share of settlements whose attributed cause set
                                 equals the injected gap-axis cause set, exactly.
                                 Set equality, not overlap: naming fee drift AND a
                                 phantom adjustment is wrong, not 50% right.

    rupee_attribution_error      total absolute paise misattributed, summed over
                                 causes, plus error on the unexplained residual.
                                 Reported in paise and as a share of the true gap
                                 magnitude, so "we found the cause but sized it wrong"
                                 is visible rather than hidden inside a set match.
"""

from __future__ import annotations

from dataclasses import dataclass

from afc.core.decompose import Decomposition
from afc.core.models import SettlementTruth


@dataclass(frozen=True)
class AttributionScore:
    settlements_scored: int
    exact_cause_set_matches: int
    decomposition_failures: int
    rupee_attribution_error_paise: int
    true_gap_magnitude_paise: int
    disagreements: tuple[tuple[str, frozenset[str], frozenset[str]], ...]

    @property
    def exact_cause_set_match_rate(self) -> float:
        return (
            self.exact_cause_set_matches / self.settlements_scored
            if self.settlements_scored
            else 0.0
        )

    @property
    def rupee_attribution_error_rate(self) -> float:
        return (
            self.rupee_attribution_error_paise / self.true_gap_magnitude_paise
            if self.true_gap_magnitude_paise
            else 0.0
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "settlements_scored": self.settlements_scored,
            "exact_cause_set_match_rate": round(self.exact_cause_set_match_rate, 6),
            "exact_cause_set_matches": self.exact_cause_set_matches,
            "decomposition_failures": self.decomposition_failures,
            "rupee_attribution_error_paise": self.rupee_attribution_error_paise,
            "rupee_attribution_error_rate": round(self.rupee_attribution_error_rate, 6),
            "true_gap_magnitude_paise": self.true_gap_magnitude_paise,
        }


def score(
    decompositions: tuple[Decomposition, ...], truth: list[SettlementTruth]
) -> AttributionScore:
    by_id = {t.settlement_id: t for t in truth}
    scored = matches = failures = 0
    error = 0
    magnitude = 0
    disagreements: list[tuple[str, frozenset[str], frozenset[str]]] = []

    for d in decompositions:
        t = by_id.get(d.settlement_id)
        if t is None:
            continue  # no bank credit: no gap exists, nothing to attribute
        scored += 1
        if d.failed:
            failures += 1
            # A failed decomposition attributes nothing, so the whole true gap is error.
            error += abs(t.expected_gap_paise)
            magnitude += abs(t.expected_gap_paise)
            disagreements.append((d.settlement_id, frozenset(), frozenset(
                c for c, _ in t.expected_attribution)))
            continue

        expected_causes = frozenset(c for c, _ in t.expected_attribution)
        if d.cause_set == expected_causes:
            matches += 1
        else:
            disagreements.append((d.settlement_id, d.cause_set, expected_causes))

        expected_amounts = dict(t.expected_attribution)
        actual_amounts = {a.cause: a.amount_paise for a in d.attributed}
        for cause in set(expected_amounts) | set(actual_amounts):
            error += abs(actual_amounts.get(cause, 0) - expected_amounts.get(cause, 0))
        error += abs(d.unexplained_paise - t.expected_unexplained_paise)
        magnitude += abs(t.expected_gap_paise)

    return AttributionScore(
        settlements_scored=scored,
        exact_cause_set_matches=matches,
        decomposition_failures=failures,
        rupee_attribution_error_paise=error,
        true_gap_magnitude_paise=magnitude,
        disagreements=tuple(disagreements),
    )
