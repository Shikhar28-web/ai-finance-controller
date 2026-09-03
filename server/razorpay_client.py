"""Razorpay Test Mode API client.

Fetches payments, settlements, and refunds from Razorpay's API and normalizes
them into the afc.core.models schema so the existing pipeline can process them
without modification.
"""

from __future__ import annotations

import razorpay
from datetime import date, datetime


class RazorpayClient:
    """Wraps the Razorpay Python SDK for fetching test-mode transaction data."""

    def __init__(self, key_id: str, key_secret: str):
        self.client = razorpay.Client(auth=(key_id, key_secret))
        self.key_id = key_id

    def verify_connection(self) -> bool:
        """Test that the API keys are valid."""
        try:
            self.client.payment.all({"count": 1})
            return True
        except Exception:
            return False

    def fetch_payments(self, count: int = 100, skip: int = 0) -> list[dict]:
        """Fetch payments from Razorpay API."""
        try:
            result = self.client.payment.all({"count": count, "skip": skip})
            return result.get("items", [])
        except Exception as e:
            print(f"Error fetching payments: {e}")
            return []

    def fetch_all_payments(self, max_pages: int = 10) -> list[dict]:
        """Fetch all payments, paginating through results."""
        all_payments = []
        for page in range(max_pages):
            batch = self.fetch_payments(count=100, skip=page * 100)
            all_payments.extend(batch)
            if len(batch) < 100:
                break
        return all_payments

    def fetch_settlements(self, count: int = 100, skip: int = 0) -> list[dict]:
        """Fetch settlements from Razorpay API."""
        try:
            result = self.client.settlement.all({"count": count, "skip": skip})
            return result.get("items", [])
        except Exception as e:
            print(f"Error fetching settlements: {e}")
            return []

    def fetch_refunds(self, count: int = 100, skip: int = 0) -> list[dict]:
        """Fetch refunds from Razorpay API."""
        try:
            result = self.client.refund.all({"count": count, "skip": skip})
            return result.get("items", [])
        except Exception as e:
            print(f"Error fetching refunds: {e}")
            return []

    def fetch_all_data(self) -> dict:
        """Fetch all payments, settlements, and refunds."""
        payments = self.fetch_all_payments()
        settlements = self.fetch_settlements(count=100)
        refunds = self.fetch_refunds(count=100)

        return {
            "payments": payments,
            "settlements": settlements,
            "refunds": refunds,
            "counts": {
                "payments": len(payments),
                "settlements": len(settlements),
                "refunds": len(refunds),
            },
        }


def normalize_razorpay_data(raw: dict) -> dict:
    """Convert Razorpay API JSON into the format expected by our pipeline.

    Returns a dict with lists of normalized records ready for the reconciliation
    engine.
    """
    payments = []
    for p in raw.get("payments", []):
        if p.get("status") != "captured":
            continue

        # Razorpay amounts are in paise
        amount_paise = p.get("amount", 0)
        created_at = p.get("created_at", 0)
        captured_dt = datetime.fromtimestamp(created_at).date() if created_at else date.today()

        # Extract settlement ID if available
        settlement_id = None
        if p.get("settlement_id"):
            settlement_id = p["settlement_id"]

        payments.append({
            "payment_id": p["id"],
            "order_id": p.get("order_id", "") or p["id"],
            "gross_paise": amount_paise,
            "captured_on": captured_dt.isoformat(),
            "applied_mdr_bps": 190,  # Default contracted MDR
            "settlement_id": settlement_id or "",
            "method": p.get("method", ""),
            "status": p.get("status", ""),
            "email": p.get("email", ""),
            "contact": p.get("contact", ""),
            "description": p.get("description", ""),
        })

    settlements = []
    for s in raw.get("settlements", []):
        created_at = s.get("created_at", 0)
        settled_dt = datetime.fromtimestamp(created_at).date() if created_at else date.today()

        settlements.append({
            "settlement_id": s["id"],
            "settled_on": settled_dt.isoformat(),
            "utr": s.get("utr", "") or "",
            "amount_paise": s.get("amount", 0),
            "status": s.get("status", ""),
        })

    refunds = []
    for r in raw.get("refunds", []):
        created_at = r.get("created_at", 0)
        created_dt = datetime.fromtimestamp(created_at).date() if created_at else date.today()

        refunds.append({
            "refund_id": r["id"],
            "payment_id": r.get("payment_id", ""),
            "amount_paise": r.get("amount", 0),
            "created_on": created_dt.isoformat(),
            "status": r.get("status", ""),
        })

    return {
        "payments": payments,
        "settlements": settlements,
        "refunds": refunds,
    }
