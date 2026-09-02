"""Normalize the three source files into the common schema. SPEC 10 step 2.

SPEC 10 says "all three sources load into one table". Read as one common schema --
one money convention (integer paise), one date convention (ISO), one identifier
convention -- not one physical table: payments, bank rows and ledger rows have
different cardinality, and flattening them would destroy the Level 0 duplicate check,
which is a group-by on exactly those differing keys.

Money arrives as rupee strings and is parsed by afc.money.parse_paise, which refuses
more than two decimal places rather than guessing. No float is constructed anywhere.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from afc.core.models import (
    BankRow,
    LedgerEntry,
    Order,
    Payment,
    RateCard,
    Refund,
    Settlement,
)
from afc.generate import emit
from afc.ingest.narration import extract_all
from afc.money import parse_paise


@dataclass(frozen=True)
class NormalizedBatch:
    """Everything the matcher needs, in one money and date convention."""

    seed: int
    as_of: date
    rate_card: RateCard
    holidays: frozenset[date]
    payments: list[Payment]
    settlements: list[Settlement]
    refunds: list[Refund]
    bank_rows: list[BankRow]
    ledger: list[LedgerEntry]
    utr_by_bank_row: dict[str, str | None]

    @property
    def orders(self) -> list[Order]:
        """Orders are recoverable from payments; kept for Level 1's order_id check."""
        return [Order(p.order_id, p.gross_paise, p.captured_on) for p in self.payments]


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def load(src: Path) -> NormalizedBatch:
    manifest = json.loads((src / emit.MANIFEST).read_text())

    payments = [
        Payment(r["payment_id"], r["order_id"], parse_paise(r["gross"]),
                date.fromisoformat(r["captured_on"]), int(r["applied_mdr_bps"]),
                r["settlement_id"] or None)
        for r in _rows(src / emit.RAZORPAY_PAYMENTS)
    ]
    settlements = [
        Settlement(r["settlement_id"], date.fromisoformat(r["settled_on"]),
                   r["utr"] or None, date.fromisoformat(r["due_on"]))
        for r in _rows(src / emit.RAZORPAY_SETTLEMENTS)
    ]
    refunds = [
        Refund(r["refund_id"], r["payment_id"], parse_paise(r["amount"]),
               date.fromisoformat(r["created_on"]))
        for r in _rows(src / emit.RAZORPAY_REFUNDS)
    ]
    bank_raw = _rows(src / emit.BANK_STATEMENT)
    bank_rows = [
        BankRow(r["bank_row_id"], date.fromisoformat(r["value_date"]), r["narration"],
                parse_paise(r["credit"]))
        for r in bank_raw
    ]
    ledger = [
        LedgerEntry(r["ledger_id"], r["payment_id"], parse_paise(r["amount"]),
                    date.fromisoformat(r["booked_on"]))
        for r in _rows(src / emit.MERCHANT_LEDGER)
    ]

    # Raises on any coverage shortfall rather than letting blanks flow through as
    # phantom missing_utr faults.
    extractions = extract_all(
        [b.narration for b in bank_rows], manifest["bank_rows_with_utr"]
    )
    utr_by_bank_row = {
        b.bank_row_id: e.utr for b, e in zip(bank_rows, extractions, strict=True)
    }

    return NormalizedBatch(
        seed=manifest["seed"],
        as_of=date.fromisoformat(manifest["as_of"]),
        rate_card=RateCard(manifest["contracted_mdr_bps"], manifest["gst_on_fee_bps"]),
        holidays=frozenset(
            date.fromisoformat(d) for d in manifest["calendar"]["holidays"]
        ),
        payments=payments,
        settlements=settlements,
        refunds=refunds,
        bank_rows=bank_rows,
        ledger=ledger,
        utr_by_bank_row=utr_by_bank_row,
    )
