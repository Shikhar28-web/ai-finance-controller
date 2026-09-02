"""Deterministic confidence. SPEC 8.

A pure function of which checks passed. Never produced by an LLM, and the import
linter makes that unrepresentable rather than merely asserted.

N/A CHECKS ARE EXCLUDED FROM THE DENOMINATOR, NOT COUNTED AS PASSES. An orphan bank
credit has no payment, so `payment_found` is not a check it failed -- it is a check that
does not apply. Counting inapplicable checks as passes would score orphans near 1.0,
which is the opposite of the truth: an orphan is the record we know least about. Where
no check applies at all, confidence is 0.0 and `applicable` is 0, so the UI can say
"no evidence" rather than print a number.

CONFIDENCE GATES THE ACTION, NOT THE LABEL. The state comes from the SPEC 7 cascade.
Confidence decides whether a record may be auto-reconciled, and only states that are
eligible for automation are ever gated. The two are orthogonal by design: SPEC 9's
"cost of false positives" is about the auto-reconcile decision, and SPEC 7 is about the
classification.
"""

from __future__ import annotations

from dataclasses import dataclass

from afc.config import (
    AUTO_THRESHOLD,
    CHECK_NAMES,
    CHECK_WEIGHTS,
    EXPLAINED,
    REVIEW_THRESHOLD,
    VERIFIED,
)

AUTO_RECONCILE = "auto_reconcile"
AI_SUGGESTION = "ai_suggestion_human_confirms"
HUMAN_QUEUE = "human_review_queue"

# Only these states are eligible for automation at all; confidence gates within them.
AUTOMATABLE_STATES = frozenset({VERIFIED, EXPLAINED})

NA = None


@dataclass(frozen=True)
class Confidence:
    checks: tuple[tuple[str, bool | None], ...]   # None means not applicable
    score: float
    applicable: int
    passed: int

    def as_dict(self) -> dict[str, object]:
        return {
            "score": round(self.score, 6),
            "passed": self.passed,
            "applicable": self.applicable,
            "checks": {name: value for name, value in self.checks},
        }


def score(results: dict[str, bool | None]) -> Confidence:
    """Weighted pass rate over applicable checks only."""
    ordered = tuple((name, results.get(name)) for name in CHECK_NAMES)
    applicable = [(n, v) for n, v in ordered if v is not None]
    total_weight = sum(CHECK_WEIGHTS[n] for n, _ in applicable)
    passed_weight = sum(CHECK_WEIGHTS[n] for n, v in applicable if v)
    return Confidence(
        checks=ordered,
        score=(passed_weight / total_weight) if total_weight else 0.0,
        applicable=len(applicable),
        passed=sum(1 for _, v in applicable if v),
    )


def route(state: str, confidence: Confidence) -> str:
    """SPEC 8 routing. A state outside AUTOMATABLE_STATES never auto-reconciles,
    however high its confidence -- product rule 3."""
    if state not in AUTOMATABLE_STATES:
        return HUMAN_QUEUE if confidence.score < REVIEW_THRESHOLD else AI_SUGGESTION
    if confidence.score >= AUTO_THRESHOLD:
        return AUTO_RECONCILE
    if confidence.score >= REVIEW_THRESHOLD:
        return AI_SUGGESTION
    return HUMAN_QUEUE
