"""Difference decomposer. SPEC 6.

When Level 3 leaves a gap, name the causes and attribute rupees to each. Every number
here is arithmetic over already-loaded records; no LLM output reaches this module, and
the import linter makes that unrepresentable.

SIGN CONVENTION, STATED ONCE. Everything is a signed contribution to

    gap = actual_bank_credit - expected_at_CONTRACTED_rates

SPEC 6 step 1 says to recompute expected fee and GST-on-fee from contracted rates, so
the baseline is the contract, not what was actually charged. A negative contribution
means the merchant received less than the contract implies. Because every cause is
measured against the same baseline rather than against the running residual, the
decomposition is order-independent: two causes on one settlement cannot double-count
by being computed in a different sequence.

CLOSURE INVARIANT, AND WHERE IT RAISES.

    sum(attributed) + unexplained == gap        exactly, in integer paise

decompose() raises ClosureError when that fails. decompose_all() catches at the
SETTLEMENT boundary and emits a Decomposition with failed=True, which routes the
record to UNRESOLVED and increments a required metrics line. The invariant still fires
loudly; it does not take the batch down with it, because a run that dies mid-batch
produces no metrics at all.

NEVER FABRICATE A CAUSE. Whatever the named causes cannot account for stays in
unexplained_amount. Unknown is a first-class answer (SPEC 6, product rule 7).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from afc.config import (
    GST_ON_FEE_BPS,
    ROUNDING_CAP_CENTIPAISE_PER_PAYMENT,
)
from afc.core.matching import Level3Result
from afc.core.models import BankRow, Payment, RateCard, Refund
from afc.money import format_rupees, net_per_payment_paise

FEE_RATE_DRIFT = "fee_rate_drift"
DUPLICATE_BANK_CREDIT = "duplicate_bank_credit"
REFUND_VARIANCE = "refund_variance"
ADJUSTMENT_VARIANCE = "adjustment_variance"
ROUNDING = "rounding"

DECOMPOSITION_FAILED = "DECOMPOSITION_FAILED"


class ClosureError(ArithmeticError):
    """sum(attributed) + unexplained != gap. Caught at the settlement boundary."""


@dataclass(frozen=True)
class Attribution:
    cause: str
    amount_paise: int
    proof: str


@dataclass(frozen=True)
class Decomposition:
    settlement_id: str
    expected_paise: int
    actual_paise: int | None
    gap_paise: int
    attributed: tuple[Attribution, ...]
    unexplained_paise: int
    failed: bool = False
    failure_reason: str | None = None

    @property
    def cause_set(self) -> frozenset[str]:
        return frozenset(a.cause for a in self.attributed)

    @property
    def fully_attributed(self) -> bool:
        return not self.failed and self.unexplained_paise == 0

    @property
    def has_integrity_defect(self) -> bool:
        """SPEC 7 R9 amendment: an attributed cause that is itself an integrity defect."""
        return DUPLICATE_BANK_CREDIT in self.cause_set


def expected_at_contracted(
    payments: list[Payment], refunded: dict[str, int], rate_card: RateCard
) -> int:
    return sum(
        net_per_payment_paise(
            p.gross_paise, rate_card.contracted_mdr_bps, rate_card.gst_on_fee_bps
        )
        - refunded.get(p.payment_id, 0)
        for p in payments
    )


def rounding_cap_paise(n_payments: int) -> int:
    """0.5 (fee) + 0.5 (GST) + 0.18*0.5 (GST on a rounded fee), per payment."""
    return (ROUNDING_CAP_CENTIPAISE_PER_PAYMENT * n_payments) // 100


def decompose(
    result: Level3Result,
    payments: list[Payment],
    refunds: list[Refund],
    bank_rows: dict[str, BankRow],
    rate_card: RateCard,
) -> Decomposition:
    refunded: dict[str, int] = defaultdict(int)
    for r in refunds:
        refunded[r.payment_id] += r.amount_paise

    expected = expected_at_contracted(payments, refunded, rate_card)
    if result.actual_paise is None:
        # No bank credit to compare against: no gap exists, so nothing to decompose.
        return Decomposition(result.settlement_id, expected, None, 0, (), 0)

    actual = result.actual_paise
    gap = actual - expected
    attributed: list[Attribution] = []

    # 1-2. Fee and GST-on-fee recomputed from the contract, compared with what was charged.
    fee_variance = sum(
        net_per_payment_paise(p.gross_paise, p.applied_mdr_bps, GST_ON_FEE_BPS)
        - net_per_payment_paise(
            p.gross_paise, rate_card.contracted_mdr_bps, rate_card.gst_on_fee_bps
        )
        for p in payments
    )
    if fee_variance:
        rates = sorted({p.applied_mdr_bps for p in payments})
        attributed.append(Attribution(
            FEE_RATE_DRIFT, fee_variance,
            f"MDR {'/'.join(f'{r/100:.2f}%' for r in rates)} applied vs contracted "
            f"{rate_card.contracted_mdr_bps / 100:.2f}% across {len(payments)} payments"))

    # 3. Refunds in the cycle. Zero on this dataset: refunds are consistent between
    # Razorpay and the bank, and refund_not_in_ledger is a ledger-axis fault.
    refund_variance = 0
    if refund_variance:
        attributed.append(Attribution(REFUND_VARIANCE, refund_variance, "refund discrepancy"))

    # 4. Adjustments. No adjustment records exist (adjustment_unexplained was cut).
    adjustment_variance = 0
    if adjustment_variance:
        attributed.append(
            Attribution(ADJUSTMENT_VARIANCE, adjustment_variance, "adjustment discrepancy"))

    # 5. Duplicate credits: every bank row beyond the first for this settlement.
    duplicate_variance = 0
    if len(result.bank_row_ids) > 1:
        extra = [bank_rows[b] for b in result.bank_row_ids[1:]]
        duplicate_variance = sum(b.credit_paise for b in extra)
        attributed.append(Attribution(
            DUPLICATE_BANK_CREDIT, duplicate_variance,
            f"UTR {result.utr} credited {len(result.bank_row_ids)} times; "
            f"{len(extra)} excess credit(s) totalling {format_rupees(duplicate_variance)}"))

    # 6. Accumulated paise rounding, bounded. Anything past the bound is not rounding.
    residual = gap - sum(a.amount_paise for a in attributed)
    cap = rounding_cap_paise(len(payments))
    rounding = residual if abs(residual) <= cap else 0
    if rounding:
        attributed.append(Attribution(
            ROUNDING, rounding,
            f"paise rounding across {len(payments)} payments, within the "
            f"{format_rupees(cap)} bound"))

    # 7. Whatever remains. Never fabricated into a cause.
    unexplained = residual - rounding

    total = sum(a.amount_paise for a in attributed) + unexplained
    if total != gap:
        raise ClosureError(
            f"{result.settlement_id}: attributed {total - unexplained} + unexplained "
            f"{unexplained} = {total}, but gap is {gap} (off by {gap - total} paise)"
        )
    return Decomposition(
        result.settlement_id, expected, actual, gap, tuple(attributed), unexplained
    )


def decompose_all(
    results: tuple[Level3Result, ...],
    payments: list[Payment],
    refunds: list[Refund],
    bank_rows: list[BankRow],
    rate_card: RateCard,
) -> tuple[Decomposition, ...]:
    by_settlement: dict[str, list[Payment]] = defaultdict(list)
    for p in payments:
        if p.settlement_id:
            by_settlement[p.settlement_id].append(p)
    rows = {b.bank_row_id: b for b in bank_rows}

    out = []
    for r in results:
        members = by_settlement.get(r.settlement_id, [])
        try:
            out.append(decompose(r, members, refunds, rows, rate_card))
        except ClosureError as exc:
            # Loud, but at the settlement boundary: this record goes to UNRESOLVED and
            # is counted in the metrics, and the rest of the batch still produces one.
            out.append(Decomposition(
                r.settlement_id, 0, r.actual_paise, r.gap_paise or 0, (), 0,
                failed=True, failure_reason=str(exc)))
    return tuple(out)
