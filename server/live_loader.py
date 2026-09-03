"""Bridge between live data (Razorpay API + bank uploads) and the existing pipeline.

Creates a NormalizedBatch from real Razorpay data + uploaded bank statements,
instead of from the synthetic generator, so the existing pipeline.run() works
without modification.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from afc.config import CONTRACTED_MDR_BPS, GST_ON_FEE_BPS
from afc.core.calendar import add_working_days
from afc.core.holidays import holiday_set
from afc.core.models import (
    BankRow,
    LedgerEntry,
    Payment,
    RateCard,
    Refund,
    Settlement,
)
from afc.ingest.loader import NormalizedBatch
from afc.ingest.narration import extract_utr

from server.bank_parser import ParsedBankRow


T_PLUS = 2


def build_normalized_batch(
    razorpay_data: dict,
    bank_rows: list[ParsedBankRow],
    as_of: date | None = None,
) -> NormalizedBatch:
    """Convert live Razorpay data + bank statement rows into a NormalizedBatch.

    This allows the existing pipeline.run() to process real data exactly the same
    way it processes synthetic data.
    """
    if as_of is None:
        as_of = date.today()

    # Build holiday set for the relevant year range
    years = set()
    years.add(as_of.year)
    for p in razorpay_data.get("payments", []):
        try:
            years.add(date.fromisoformat(p["captured_on"]).year)
        except (ValueError, KeyError):
            pass
    holidays = frozenset()
    for y in years:
        holidays = holidays | holiday_set(y, y)

    rate_card = RateCard(CONTRACTED_MDR_BPS, GST_ON_FEE_BPS)

    # Build Payments
    payments = []
    for p in razorpay_data.get("payments", []):
        try:
            payments.append(Payment(
                payment_id=p["payment_id"],
                order_id=p.get("order_id", "") or p["payment_id"],
                gross_paise=p["gross_paise"],
                captured_on=date.fromisoformat(p["captured_on"]),
                applied_mdr_bps=p.get("applied_mdr_bps", CONTRACTED_MDR_BPS),
                settlement_id=p.get("settlement_id") or None,
            ))
        except (ValueError, KeyError) as e:
            print(f"Skipping payment {p.get('payment_id', '?')}: {e}")

    # Build Settlements
    settlements = []
    for s in razorpay_data.get("settlements", []):
        try:
            settled_on = date.fromisoformat(s["settled_on"])
            due_on = add_working_days(settled_on, T_PLUS, holidays)
            settlements.append(Settlement(
                settlement_id=s["settlement_id"],
                settled_on=settled_on,
                utr=s.get("utr") or None,
                due_on=due_on,
            ))
        except (ValueError, KeyError) as e:
            print(f"Skipping settlement {s.get('settlement_id', '?')}: {e}")

    # Build Refunds
    refunds = []
    for r in razorpay_data.get("refunds", []):
        try:
            refunds.append(Refund(
                refund_id=r["refund_id"],
                payment_id=r["payment_id"],
                amount_paise=r["amount_paise"],
                created_on=date.fromisoformat(r["created_on"]),
            ))
        except (ValueError, KeyError) as e:
            print(f"Skipping refund {r.get('refund_id', '?')}: {e}")

    # Build BankRows from parsed bank statement data
    afc_bank_rows = []
    for br in bank_rows:
        afc_bank_rows.append(BankRow(
            bank_row_id=br.row_id,
            value_date=br.value_date,
            narration=br.narration,
            credit_paise=br.credit_paise,
        ))

    # Extract UTRs from narrations
    utr_by_bank_row: dict[str, str | None] = {}
    for br in afc_bank_rows:
        utr_by_bank_row[br.bank_row_id] = extract_utr(br.narration)

    # Build synthetic ledger entries from payments (since we don't have a real ledger)
    # In a real system, the user would also upload their accounting ledger.
    # For now, we create expected ledger entries from the payments.
    from afc.money import net_per_payment_paise

    ledger = []
    for i, p in enumerate(payments):
        net = net_per_payment_paise(p.gross_paise, p.applied_mdr_bps, GST_ON_FEE_BPS)
        # Subtract any refunds
        refund_total = sum(r.amount_paise for r in refunds if r.payment_id == p.payment_id)
        booked = net - refund_total
        ledger.append(LedgerEntry(
            ledger_id=f"led_live_{i:05d}",
            payment_id=p.payment_id,
            amount_paise=booked,
            booked_on=p.captured_on,
        ))

    return NormalizedBatch(
        seed=0,  # Not applicable for live data
        as_of=as_of,
        rate_card=rate_card,
        holidays=holidays,
        payments=payments,
        settlements=settlements,
        refunds=refunds,
        bank_rows=afc_bank_rows,
        ledger=ledger,
        utr_by_bank_row=utr_by_bank_row,
    )
