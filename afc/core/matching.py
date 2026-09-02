"""Levels 1 and 3. SPEC 5.

Level 2 is not implemented. SPEC 5 defines it as checking that a settlement's payment
count and gross total equal the sum of its constituent payments; because the generator
builds settlements by grouping its own payments, that check is tautologically true and
no fault in the table can break it. It would have reported 100% while doing no work.
This project ships Level 0 integrity plus two matching levels and does not claim a
three-level matcher.

LEVEL 1 CONSUMES LEVEL 0, IT DOES NOT RE-DERIVE.
Duplicate status arrives as a set of flagged payment ids from afc.core.integrity. If
Level 1 recomputed it, the two could drift and the cascade would see inconsistent
evidence for one record. A test mutates a Level 0 finding and asserts Level 1's output
follows.

LEVEL 3 TIE-BREAK POLICY, STATED.
When a settlement carries a UTR, the match is KEYED: join on UTR, and cardinality
comes from Level 0 rather than from taking a row.

When the UTR is missing, fall back to amount-within-tolerance AND date-within-window
over the bank rows that carry no extractable UTR. Then:

    exactly one candidate   -> INFERRED match (SPEC 7 R4 sends it to HUMAN_REVIEW)
    zero candidates         -> UNMATCHED_NO_CANDIDATE
    more than one candidate -> UNMATCHED_AMBIGUOUS

"Ambiguous" means more than one bank row satisfies BOTH the amount tolerance and the
date window. An ambiguous settlement is an UNMATCHED RECORD routed by R1 or R3. It is
never resolved by a pick: no nearest-neighbour, no first-in-file, no lowest-id. Any of
those would make the result depend on row order, and sealed_eval would stop being
reproducible -- an irreproducible measurement is worse than an unmatched record.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from afc.config import GST_ON_FEE_BPS, INFERRED_MATCH_WINDOW_WORKING_DAYS, TOLERANCE_PAISE
from afc.core.calendar import working_days_between
from afc.core.integrity import IntegrityReport
from afc.core.models import BankRow, LedgerEntry, Payment, Refund, Settlement
from afc.money import net_per_payment_paise

KEYED = "keyed"
INFERRED = "inferred"
UNMATCHED_NO_BANK_ROW = "unmatched_no_bank_row"
UNMATCHED_NO_CANDIDATE = "unmatched_no_candidate"
UNMATCHED_AMBIGUOUS = "unmatched_ambiguous"

MATCHED_OUTCOMES = frozenset({KEYED, INFERRED})


@dataclass(frozen=True)
class Level1Result:
    payment_id: str
    order_id: str
    order_id_matches: bool
    ledger_found: bool
    ledger_duplicate: bool          # from Level 0; never recomputed here
    expected_ledger_paise: int
    booked_ledger_paise: int | None

    @property
    def ledger_agrees(self) -> bool:
        """SPEC 5 Level 1: amounts must agree EXACTLY at payment grain.

        Tolerance is a settlement-aggregate concept (SPEC 7); there is no rounding to
        absorb at this grain, so an exact test is the honest one.
        """
        if not self.ledger_found or self.ledger_duplicate:
            return False
        return self.booked_ledger_paise == self.expected_ledger_paise


@dataclass(frozen=True)
class Level3Result:
    settlement_id: str
    outcome: str
    utr: str | None
    bank_row_ids: tuple[str, ...]
    expected_paise: int
    actual_paise: int | None
    duplicate_credit: bool          # from Level 0
    candidates_considered: int

    @property
    def gap_paise(self) -> int | None:
        return None if self.actual_paise is None else self.actual_paise - self.expected_paise

    @property
    def matched(self) -> bool:
        return self.outcome in MATCHED_OUTCOMES


@dataclass(frozen=True)
class MatchReport:
    level1: tuple[Level1Result, ...]
    level3: tuple[Level3Result, ...]
    orphan_bank_row_ids: tuple[str, ...]

    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for r in self.level3:
            counts[r.outcome] += 1
        return dict(sorted(counts.items()))


def expected_ledger_amount(payment: Payment, refunded_paise: int) -> int:
    """What the merchant should have booked: the net actually received, less refunds.

    Uses the APPLIED rate, not the contracted one. The merchant books what reached the
    bank, so fee drift is a gap-axis fault and must not also register as a ledger
    disagreement -- that would double-count one injected fault across two axes.
    """
    return net_per_payment_paise(payment.gross_paise, payment.applied_mdr_bps, GST_ON_FEE_BPS) - (
        refunded_paise
    )


def match_level1(
    payments: list[Payment],
    ledger: list[LedgerEntry],
    refunds: list[Refund],
    integrity: IntegrityReport,
) -> tuple[Level1Result, ...]:
    refunded: dict[str, int] = defaultdict(int)
    for r in refunds:
        refunded[r.payment_id] += r.amount_paise

    by_payment: dict[str, list[LedgerEntry]] = defaultdict(list)
    for entry in ledger:
        by_payment[entry.payment_id].append(entry)

    flagged = integrity.flagged_payment_ids
    out = []
    for p in sorted(payments, key=lambda x: x.payment_id):
        rows = by_payment.get(p.payment_id, [])
        is_duplicate = p.payment_id in flagged
        booked = rows[0].amount_paise if len(rows) == 1 else None
        out.append(
            Level1Result(
                payment_id=p.payment_id,
                order_id=p.order_id,
                order_id_matches=bool(p.order_id),
                ledger_found=bool(rows),
                ledger_duplicate=is_duplicate,
                expected_ledger_paise=expected_ledger_amount(p, refunded[p.payment_id]),
                booked_ledger_paise=booked,
            )
        )
    return tuple(out)


def expected_bank_credit(
    payments: list[Payment], refunded: dict[str, int]
) -> int:
    """SPEC 4: sum of per-payment net, less refunds in the cycle.

    GST is computed per payment and summed, exactly as SPEC 4 writes it. Adjustments
    are always zero on this dataset (adjustment_unexplained was cut), so the term is
    omitted rather than faked.
    """
    return sum(
        net_per_payment_paise(p.gross_paise, p.applied_mdr_bps, GST_ON_FEE_BPS)
        - refunded.get(p.payment_id, 0)
        for p in payments
    )


def match_level3(
    settlements: list[Settlement],
    payments: list[Payment],
    refunds: list[Refund],
    bank_rows: list[BankRow],
    utr_by_bank_row: dict[str, str | None],
    integrity: IntegrityReport,
    holidays: frozenset,
) -> tuple[tuple[Level3Result, ...], tuple[str, ...]]:
    refunded: dict[str, int] = defaultdict(int)
    for r in refunds:
        refunded[r.payment_id] += r.amount_paise

    by_settlement: dict[str, list[Payment]] = defaultdict(list)
    for p in payments:
        if p.settlement_id:
            by_settlement[p.settlement_id].append(p)

    rows_by_utr: dict[str, list[BankRow]] = defaultdict(list)
    unkeyed_rows: list[BankRow] = []
    for row in sorted(bank_rows, key=lambda b: b.bank_row_id):
        utr = utr_by_bank_row.get(row.bank_row_id)
        (unkeyed_rows if utr is None else rows_by_utr[utr]).append(row)

    duplicated_utrs = integrity.flagged_utrs
    consumed_unkeyed: set[str] = set()
    results = []

    for s in sorted(settlements, key=lambda x: x.settlement_id):
        members = by_settlement.get(s.settlement_id, [])
        expected = expected_bank_credit(members, refunded)

        if s.utr is not None:
            rows = rows_by_utr.get(s.utr, [])
            if not rows:
                results.append(Level3Result(s.settlement_id, UNMATCHED_NO_BANK_ROW, s.utr,
                                            (), expected, None, False, 0))
                continue
            # Cardinality comes from Level 0, not from taking a row.
            results.append(Level3Result(
                s.settlement_id, KEYED, s.utr,
                tuple(b.bank_row_id for b in rows), expected,
                sum(b.credit_paise for b in rows),
                s.utr in duplicated_utrs, len(rows)))
            continue

        candidates = [
            b for b in unkeyed_rows
            if b.bank_row_id not in consumed_unkeyed
            and abs(b.credit_paise - expected) <= TOLERANCE_PAISE
            and abs(working_days_between(s.due_on, b.value_date, holidays))
            <= INFERRED_MATCH_WINDOW_WORKING_DAYS
        ]
        if len(candidates) == 1:
            row = candidates[0]
            consumed_unkeyed.add(row.bank_row_id)
            results.append(Level3Result(s.settlement_id, INFERRED, None,
                                        (row.bank_row_id,), expected,
                                        row.credit_paise, False, 1))
        else:
            outcome = UNMATCHED_AMBIGUOUS if candidates else UNMATCHED_NO_CANDIDATE
            results.append(Level3Result(s.settlement_id, outcome, None, (), expected,
                                        None, False, len(candidates)))

    settlement_utrs = {s.utr for s in settlements if s.utr}
    orphans = [
        b.bank_row_id
        for b in bank_rows
        if (utr_by_bank_row.get(b.bank_row_id) not in settlement_utrs)
        and b.bank_row_id not in consumed_unkeyed
    ]
    return tuple(results), tuple(sorted(orphans))


def run(
    payments: list[Payment],
    settlements: list[Settlement],
    ledger: list[LedgerEntry],
    refunds: list[Refund],
    bank_rows: list[BankRow],
    utr_by_bank_row: dict[str, str | None],
    integrity: IntegrityReport,
    holidays: frozenset,
) -> MatchReport:
    level3, orphans = match_level3(
        settlements, payments, refunds, bank_rows, utr_by_bank_row, integrity, holidays
    )
    return MatchReport(
        level1=match_level1(payments, ledger, refunds, integrity),
        level3=level3,
        orphan_bank_row_ids=orphans,
    )
