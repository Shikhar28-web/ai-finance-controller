"""The SPEC 7 decision table, as data, evaluated in one stated order. SPEC 7, SPEC 8.

The table lives here once, as an ordered list of (rule id, state, predicate), so it can
be read against SPEC 7 line by line. First match wins. UNRESOLVED is last as the safe
default: a record that matches nothing is never auto-reconciled.

Two amendments the owner made explicitly, both recorded in the README:
  R7 also fires when ledger_agrees is false, or when Level 0 found an integrity defect
  R9 additionally requires that no attributed cause is itself an integrity defect

DECOMPOSITION_FAILED is handled BEFORE the cascade, as an error path rather than a
state rule. A failed closure means unexplained_amount is unknown, so R5 and R6 cannot
be evaluated honestly; the record routes to UNRESOLVED and is counted on its own
metrics line.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from afc.config import (
    AWAITING_BANK,
    EXPLAINED,
    HUMAN_REVIEW,
    MATERIALITY_PAISE,
    TOLERANCE_PAISE,
    UNRESOLVED,
    VERIFIED,
)
from afc.core import confidence as conf
from afc.core.decompose import DECOMPOSITION_FAILED, Decomposition
from afc.core.matching import INFERRED, KEYED, Level1Result, Level3Result
from afc.core.models import ORPHAN_BANK_CREDIT, PAYMENTLESS_SETTLEMENT


@dataclass(frozen=True)
class Evidence:
    unit_id: str
    unit_kind: str
    level1: Level1Result | None
    level3: Level3Result | None
    decomposition: Decomposition | None
    due_on: date | None
    as_of: date
    integrity_defect: bool

    # ---- predicates, named to match the SPEC 7 wording ----
    @property
    def has_no_counterpart(self) -> bool:
        return self.unit_kind in (ORPHAN_BANK_CREDIT, PAYMENTLESS_SETTLEMENT)

    @property
    def bank_credit_absent(self) -> bool:
        return self.level3 is not None and self.level3.actual_paise is None

    @property
    def inside_window(self) -> bool:
        return self.due_on is not None and self.as_of <= self.due_on

    @property
    def match_inferred(self) -> bool:
        return self.level3 is not None and self.level3.outcome == INFERRED

    @property
    def unexplained(self) -> int:
        return self.decomposition.unexplained_paise if self.decomposition else 0

    @property
    def gap(self) -> int:
        return self.decomposition.gap_paise if self.decomposition else 0

    @property
    def attributions(self) -> int:
        return len(self.decomposition.attributed) if self.decomposition else 0

    @property
    def ledger_agrees(self) -> bool:
        return self.level1 is not None and self.level1.ledger_agrees

    @property
    def keyed(self) -> bool:
        return self.level3 is not None and self.level3.outcome == KEYED

    @property
    def integrity_cause_attributed(self) -> bool:
        return self.decomposition is not None and self.decomposition.has_integrity_defect

    @property
    def decomposition_failed(self) -> bool:
        return self.decomposition is not None and self.decomposition.failed


Rule = tuple[str, str, Callable[[Evidence], bool]]

# SPEC 7, in evaluation order. First match wins.
TABLE: tuple[Rule, ...] = (
    ("R1", UNRESOLVED, lambda e: e.has_no_counterpart),
    ("R2", AWAITING_BANK, lambda e: e.bank_credit_absent and e.inside_window),
    ("R3", UNRESOLVED, lambda e: e.bank_credit_absent and not e.inside_window),
    ("R4", HUMAN_REVIEW, lambda e: e.match_inferred),
    ("R5", UNRESOLVED, lambda e: abs(e.unexplained) >= MATERIALITY_PAISE),
    ("R6", HUMAN_REVIEW, lambda e: 0 < abs(e.unexplained) < MATERIALITY_PAISE),
    ("R7", HUMAN_REVIEW, lambda e: not e.ledger_agrees or e.integrity_defect),
    ("R8", VERIFIED, lambda e: (
        e.keyed and abs(e.gap) <= TOLERANCE_PAISE and e.attributions == 0
        and e.ledger_agrees)),
    ("R9", EXPLAINED, lambda e: (
        e.unexplained == 0 and e.attributions > 0 and not e.integrity_cause_attributed)),
    ("R10", UNRESOLVED, lambda e: True),
)


@dataclass(frozen=True)
class Decision:
    unit_id: str
    unit_kind: str
    state: str
    rule: str
    confidence: conf.Confidence
    routing: str

    def as_dict(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "unit_kind": self.unit_kind,
            "state": self.state,
            "rule": self.rule,
            "routing": self.routing,
            "confidence": self.confidence.as_dict(),
        }


def checks_for(e: Evidence) -> dict[str, bool | None]:
    """SPEC 8's nine checks. None where the check does not apply to this unit kind."""
    if e.has_no_counterpart:
        return dict.fromkeys(
            ("payment_found", "order_id_matches", "settlement_found", "utr_present",
             "utr_matches_bank", "amount_within_tol", "ledger_agrees",
             "no_duplicate_found", "gap_fully_attributed"), conf.NA)
    return {
        "payment_found": e.level1 is not None,
        "order_id_matches": bool(e.level1 and e.level1.order_id_matches),
        "settlement_found": e.level3 is not None,
        "utr_present": (e.level3.utr is not None) if e.level3 else None,
        "utr_matches_bank": e.keyed if e.level3 else None,
        "amount_within_tol": (
            abs(e.gap) <= TOLERANCE_PAISE if not e.bank_credit_absent else None),
        "ledger_agrees": e.ledger_agrees,
        "no_duplicate_found": not e.integrity_defect,
        "gap_fully_attributed": (
            e.unexplained == 0 and not e.decomposition_failed
            if not e.bank_credit_absent else None),
    }


def decide(e: Evidence) -> Decision:
    confidence = conf.score(checks_for(e))
    if e.decomposition_failed:
        # Error path, not a state rule: unexplained_amount is unknown, so R5/R6 cannot
        # be evaluated honestly. Counted separately in the metrics.
        return Decision(e.unit_id, e.unit_kind, UNRESOLVED, DECOMPOSITION_FAILED,
                        confidence, conf.route(UNRESOLVED, confidence))
    for rule_id, state, predicate in TABLE:
        if predicate(e):
            return Decision(e.unit_id, e.unit_kind, state, rule_id, confidence,
                            conf.route(state, confidence))
    raise AssertionError("R10 is unconditional; the table cannot fall through")
