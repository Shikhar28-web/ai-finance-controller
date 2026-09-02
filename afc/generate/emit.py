"""Write a Dataset out as the three source files a merchant would actually have.

The generator holds objects; the pipeline must prove it can normalise real files. So
the dataset is emitted as CSV with money as rupee strings -- the format that makes
float contamination easy -- and read back through afc.money.parse_paise.

The manifest carries what a reader needs to interpret the numbers: the seed, the
frozen as_of, the declared closed-world calendar, and the UTR count that
afc.ingest.narration asserts extraction coverage against.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from afc.config import HONESTY_CLAUSE
from afc.core.models import Dataset
from afc.money import format_rupees

RAZORPAY_PAYMENTS = "razorpay_payments.csv"
RAZORPAY_SETTLEMENTS = "razorpay_settlements.csv"
RAZORPAY_REFUNDS = "razorpay_refunds.csv"
BANK_STATEMENT = "bank_statement.csv"
MERCHANT_LEDGER = "merchant_ledger.csv"
GROUND_TRUTH = "ground_truth.csv"
MANIFEST = "manifest.json"


def _rupees(paise: int) -> str:
    """Money as a rupee string, the way a real export writes it."""
    return format_rupees(paise).replace("Rs ", "")


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def emit(ds: Dataset, out_dir: Path, targets_label: str = "default") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    _write(out_dir / RAZORPAY_PAYMENTS,
           ["payment_id", "order_id", "gross", "captured_on", "applied_mdr_bps",
            "settlement_id"],
           [[p.payment_id, p.order_id, _rupees(p.gross_paise), p.captured_on.isoformat(),
             str(p.applied_mdr_bps), p.settlement_id or ""] for p in ds.payments])

    _write(out_dir / RAZORPAY_SETTLEMENTS,
           ["settlement_id", "settled_on", "utr", "due_on"],
           [[s.settlement_id, s.settled_on.isoformat(), s.utr or "", s.due_on.isoformat()]
            for s in ds.settlements])

    _write(out_dir / RAZORPAY_REFUNDS,
           ["refund_id", "payment_id", "amount", "created_on"],
           [[r.refund_id, r.payment_id, _rupees(r.amount_paise), r.created_on.isoformat()]
            for r in ds.refunds])

    _write(out_dir / BANK_STATEMENT,
           ["bank_row_id", "value_date", "narration", "credit"],
           [[b.bank_row_id, b.value_date.isoformat(), b.narration, _rupees(b.credit_paise)]
            for b in ds.bank_rows])

    _write(out_dir / MERCHANT_LEDGER,
           ["ledger_id", "payment_id", "amount", "booked_on"],
           [[e.ledger_id, e.payment_id, _rupees(e.amount_paise), e.booked_on.isoformat()]
            for e in ds.ledger])

    _write(out_dir / GROUND_TRUTH,
           ["unit_id", "unit_kind", "true_state", "fault_class", "faults",
            "rupee_impact"],
           [[t.unit_id, t.unit_kind, t.true_state, t.fault_class, "|".join(t.faults),
             _rupees(t.rupee_impact_paise)] for t in ds.truth])

    utr_rows = sum(1 for b in ds.bank_rows if "/UTR" in b.narration.upper())
    manifest = {
        "seed": ds.seed,
        "targets": targets_label,
        "as_of": ds.as_of.isoformat(),
        "window": [ds.window_start.isoformat(), ds.window_end.isoformat()],
        "contracted_mdr_bps": ds.rate_card.contracted_mdr_bps,
        "gst_on_fee_bps": ds.rate_card.gst_on_fee_bps,
        "calendar": {
            "declared_closed_world": True,
            "note": "Working days are Sundays, 2nd/4th Saturdays and the holidays "
                    "listed here. This asserts nothing about real bank closures in "
                    "this period; the dates are coordinates, not history.",
            "holidays": sorted(d.isoformat() for d in ds.holidays),
        },
        "counts": {
            "payments": len(ds.payments), "settlements": len(ds.settlements),
            "bank_rows": len(ds.bank_rows), "ledger_entries": len(ds.ledger),
            "refunds": len(ds.refunds), "recon_units": len(ds.truth),
        },
        "bank_rows_with_utr": utr_rows,
        "honesty_clause": HONESTY_CLAUSE,
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return out_dir / MANIFEST
