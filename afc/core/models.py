"""The normalized schema. Money is integer paise everywhere; rates are integer bps.

Five source entities plus the reconciliation unit. SPEC 10 step 2 says "all three
sources load into one table"; taken as one common schema with one money convention
and one date convention, not one physical table -- payments, bank rows and ledger
rows have genuinely different cardinality, and flattening them would destroy the
Level 0 duplicate check, which is a group-by on exactly those differing keys.

RECON_UNIT is the unit of classification and therefore the denominator of every
reported metric: one per payment, one per orphan bank credit, one per payment-less
settlement. It exists because SPEC 7 says "every transaction lands in exactly one
state" while AWAITING_BANK is a property of a settlement and orphan-record is a
property of a bank row -- three different grains that a single confusion matrix
cannot count without a declared unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

PAYMENT = "payment"
ORPHAN_BANK_CREDIT = "orphan_bank_credit"
PAYMENTLESS_SETTLEMENT = "paymentless_settlement"


@dataclass(frozen=True)
class RateCard:
    """The contracted rate. fee_rate_drift is applied_mdr_bps != contracted_mdr_bps."""

    contracted_mdr_bps: int
    gst_on_fee_bps: int


@dataclass(frozen=True)
class Order:
    order_id: str
    amount_paise: int
    created_on: date


@dataclass(frozen=True)
class Payment:
    payment_id: str
    order_id: str
    gross_paise: int
    captured_on: date
    applied_mdr_bps: int          # what was actually charged; may drift from contract
    settlement_id: str | None     # None means unsettled


@dataclass(frozen=True)
class Refund:
    refund_id: str
    payment_id: str
    amount_paise: int
    created_on: date


@dataclass(frozen=True)
class Settlement:
    settlement_id: str
    settled_on: date
    utr: str | None               # None is the missing_utr fault
    due_on: date                  # settled_on + T+2 working days


@dataclass(frozen=True)
class BankRow:
    bank_row_id: str
    value_date: date
    narration: str                # carries the UTR; parsed by regex, never fuzzy-matched
    credit_paise: int


@dataclass(frozen=True)
class LedgerEntry:
    ledger_id: str
    payment_id: str
    amount_paise: int             # booked at NET, the amount the merchant received
    booked_on: date


@dataclass(frozen=True)
class GroundTruth:
    """What the generator injected. Never read by any classifier path."""

    unit_id: str
    unit_kind: str
    true_state: str
    fault_class: str              # "clean", a fault name, or "a+b" for a compound
    faults: tuple[str, ...]
    rupee_impact_paise: int


@dataclass(frozen=True)
class SettlementTruth:
    """Gap-axis ground truth at settlement grain, recorded at injection time.

    Unit-level GroundTruth mixes axes -- a compound payment's rupee_impact_paise
    includes its ledger duplicate -- so attribution cannot be scored against it. This
    records what the generator did to the settlement-to-bank arithmetic alone: the gap
    it created against the CONTRACTED baseline, and how that gap decomposes.
    """

    settlement_id: str
    gap_faults: tuple[str, ...]
    expected_gap_paise: int
    expected_attribution: tuple[tuple[str, int], ...]   # cause -> signed paise
    expected_unexplained_paise: int


@dataclass(frozen=True)
class Dataset:
    seed: int
    as_of: date
    window_start: date
    window_end: date
    rate_card: RateCard
    holidays: frozenset[date]
    orders: list[Order] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    bank_rows: list[BankRow] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    truth: list[GroundTruth] = field(default_factory=list)
    settlement_truth: list[SettlementTruth] = field(default_factory=list)
