"""Multi-bank CSV parser.

Detects bank format from column headers and normalizes to the BankRow schema.
Also flags transactions from non-Razorpay gateways (PayU, Cashfree, PhonePe, etc.).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from dataclasses import dataclass


# ---------------------------------------------------------------- Gateway patterns
GATEWAY_PATTERNS: dict[str, list[str]] = {
    "razorpay": ["RAZORPAY", "RAZRPAY", "RZPAY", "RZP"],
    "payu": ["PAYU", "PAYUBIZ"],
    "cashfree": ["CASHFREE", "CFREE"],
    "phonepe": ["PHONEPE", "PHONE PE"],
    "paytm": ["PAYTM", "ONE97", "PAYTMBIZ"],
    "stripe": ["STRIPE"],
    "billdesk": ["BILLDESK"],
    "ccavenue": ["CCAVENUE", "CCAVE"],
    "instamojo": ["INSTAMOJO"],
}


# ---------------------------------------------------------------- Bank format detection

@dataclass
class BankFormat:
    name: str
    date_col: str
    narration_col: str
    credit_col: str
    debit_col: str | None
    date_formats: list[str]
    # Some banks use a single amount column with Cr/Dr indicator
    cr_dr_col: str | None = None


KNOWN_FORMATS: list[BankFormat] = [
    BankFormat(
        name="HDFC",
        date_col="Date",
        narration_col="Narration",
        credit_col="Deposit Amt.",
        debit_col="Withdrawal Amt.",
        date_formats=["%d/%m/%y", "%d/%m/%Y"],
    ),
    BankFormat(
        name="ICICI",
        date_col="Transaction Date",
        narration_col="Transaction Remarks",
        credit_col="Transaction Amount(INR)",
        debit_col=None,
        date_formats=["%d-%m-%Y", "%d/%m/%Y"],
        cr_dr_col="Cr/Dr",
    ),
    BankFormat(
        name="SBI",
        date_col="Txn Date",
        narration_col="Description",
        credit_col="Credit",
        debit_col="Debit",
        date_formats=["%d %b %Y", "%d %B %Y", "%d-%m-%Y"],
    ),
    BankFormat(
        name="Axis",
        date_col="Tran Date",
        narration_col="PARTICULARS",
        credit_col="CR",
        debit_col="DR",
        date_formats=["%d-%m-%Y", "%d/%m/%Y"],
    ),
    BankFormat(
        name="Kotak",
        date_col="Date",
        narration_col="Description",
        credit_col="Credit (INR)",
        debit_col="Debit (INR)",
        date_formats=["%d/%m/%Y", "%d-%m-%Y"],
    ),
    BankFormat(
        name="Yes Bank",
        date_col="Transaction Date",
        narration_col="Description",
        credit_col="Credit",
        debit_col="Debit",
        date_formats=["%d-%m-%Y", "%d/%m/%Y"],
    ),
    BankFormat(
        name="PNB",
        date_col="Date",
        narration_col="Particulars",
        credit_col="Credit",
        debit_col="Debit",
        date_formats=["%d/%m/%Y", "%d-%m-%Y"],
    ),
    BankFormat(
        name="BOB",
        date_col="TRAN DATE",
        narration_col="PARTICULARS",
        credit_col="CREDIT",
        debit_col="DEBIT",
        date_formats=["%d/%m/%Y", "%d-%m-%Y"],
    ),
    # Generic fallback — common column names across banks
    BankFormat(
        name="Generic",
        date_col="date",
        narration_col="narration",
        credit_col="credit",
        debit_col="debit",
        date_formats=["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"],
    ),
]

# Also match the synthetic format our generator produces
SYNTHETIC_FORMAT = BankFormat(
    name="AFC Synthetic",
    date_col="value_date",
    narration_col="narration",
    credit_col="credit",
    debit_col=None,
    date_formats=["%Y-%m-%d"],
)


def detect_bank_format(headers: list[str]) -> BankFormat | None:
    """Detect bank format from CSV column headers."""
    normalized = [h.strip().lower() for h in headers]

    # Check synthetic format first
    if "value_date" in normalized and "narration" in normalized and "credit" in normalized:
        return SYNTHETIC_FORMAT

    for fmt in KNOWN_FORMATS:
        # Check if all required columns exist (case-insensitive)
        required = [fmt.date_col.lower(), fmt.narration_col.lower(), fmt.credit_col.lower()]
        if all(r in normalized for r in required):
            return fmt

    # Fuzzy match: try partial matches
    for fmt in KNOWN_FORMATS:
        date_match = any(fmt.date_col.lower() in h for h in normalized)
        narr_match = any(
            any(kw in h for kw in ["narr", "desc", "particular", "remark"])
            for h in normalized
        )
        credit_match = any(
            any(kw in h for kw in ["credit", "deposit", "cr"])
            for h in normalized
        )
        if date_match and narr_match and credit_match:
            return fmt

    return None


def _find_column(headers: list[str], target: str) -> int | None:
    """Find column index by case-insensitive match."""
    target_lower = target.lower().strip()
    for i, h in enumerate(headers):
        if h.strip().lower() == target_lower:
            return i
    # Partial match fallback
    for i, h in enumerate(headers):
        if target_lower in h.strip().lower():
            return i
    return None


def _parse_date(text: str, formats: list[str]) -> date | None:
    """Try multiple date formats and return the first match."""
    text = text.strip()
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Last resort: try ISO format
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_amount(text: str) -> int:
    """Parse amount string to integer paise. Returns 0 for empty/invalid."""
    text = text.strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not text or text == "-" or text == "0" or text == "0.00":
        return 0
    try:
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1].strip()
        if text.startswith("-"):
            negative = True
            text = text[1:].strip()

        if "." in text:
            whole, frac = text.split(".", 1)
            frac = frac[:2].ljust(2, "0")
            paise = int(whole or "0") * 100 + int(frac)
        else:
            paise = int(text) * 100

        return -paise if negative else paise
    except (ValueError, TypeError):
        return 0


def detect_gateway(narration: str) -> str | None:
    """Detect payment gateway from bank narration text."""
    upper = narration.upper()
    for gateway, patterns in GATEWAY_PATTERNS.items():
        if any(p in upper for p in patterns):
            return gateway
    return None


@dataclass
class ParsedBankRow:
    row_id: str
    value_date: date
    narration: str
    credit_paise: int
    bank_name: str
    gateway: str | None  # None = unknown, "razorpay" = Razorpay, "payu" = PayU, etc.
    is_credit: bool  # True if this is a credit (incoming), False if debit


@dataclass
class BankParseResult:
    rows: list[ParsedBankRow]
    bank_name: str
    total_rows: int
    credit_rows: int
    razorpay_rows: int
    other_gateway_rows: int
    unknown_rows: int
    gateway_summary: dict[str, int]  # gateway -> count


def parse_bank_csv(csv_text: str, bank_hint: str | None = None) -> BankParseResult:
    """Parse a bank statement CSV and return normalized rows.

    Args:
        csv_text: Raw CSV content as string
        bank_hint: Optional bank name hint from user

    Returns:
        BankParseResult with all parsed and classified rows
    """
    reader = csv.reader(io.StringIO(csv_text))

    # Skip empty lines and find header row
    headers = None
    for row in reader:
        if any(cell.strip() for cell in row):
            headers = [cell.strip() for cell in row]
            break

    if not headers:
        return BankParseResult([], bank_hint or "Unknown", 0, 0, 0, 0, 0, {})

    fmt = detect_bank_format(headers)
    if not fmt:
        # Use generic format with best-guess column matching
        fmt = KNOWN_FORMATS[-1]  # Generic

    bank_name = bank_hint or fmt.name

    # Find column indices
    date_idx = _find_column(headers, fmt.date_col)
    narr_idx = _find_column(headers, fmt.narration_col)
    credit_idx = _find_column(headers, fmt.credit_col)
    debit_idx = _find_column(headers, fmt.debit_col) if fmt.debit_col else None
    crdr_idx = _find_column(headers, fmt.cr_dr_col) if fmt.cr_dr_col else None

    if date_idx is None or narr_idx is None or credit_idx is None:
        # Try harder with generic matching
        for i, h in enumerate(headers):
            hl = h.lower()
            if date_idx is None and any(kw in hl for kw in ["date", "txn", "tran"]):
                date_idx = i
            if narr_idx is None and any(kw in hl for kw in ["narr", "desc", "particular", "remark"]):
                narr_idx = i
            if credit_idx is None and any(kw in hl for kw in ["credit", "deposit", "cr", "amount"]):
                credit_idx = i
            if debit_idx is None and any(kw in hl for kw in ["debit", "withdrawal", "dr"]):
                debit_idx = i

    rows: list[ParsedBankRow] = []
    gateway_counts: dict[str, int] = {}
    row_num = 0

    for raw_row in reader:
        if not any(cell.strip() for cell in raw_row):
            continue

        row_num += 1

        # Extract values safely
        def safe_get(idx: int | None) -> str:
            if idx is None or idx >= len(raw_row):
                return ""
            return raw_row[idx].strip()

        date_str = safe_get(date_idx)
        narration = safe_get(narr_idx)
        credit_str = safe_get(credit_idx)
        debit_str = safe_get(debit_idx) if debit_idx is not None else ""

        # Parse date
        parsed_date = _parse_date(date_str, fmt.date_formats)
        if not parsed_date:
            continue  # Skip rows with unparseable dates

        # Parse amount
        credit_paise = _parse_amount(credit_str)
        debit_paise = _parse_amount(debit_str)

        # Handle Cr/Dr indicator column
        is_credit = True
        if crdr_idx is not None:
            crdr = safe_get(crdr_idx).upper().strip()
            if crdr in ("DR", "D", "DEBIT"):
                is_credit = False
            credit_paise = _parse_amount(credit_str)
        elif credit_paise > 0:
            is_credit = True
        elif debit_paise > 0:
            is_credit = False
            credit_paise = debit_paise
        elif credit_paise == 0 and debit_paise == 0:
            continue  # Skip zero-value rows

        # Only keep credit rows for reconciliation (settlements are incoming)
        if not is_credit:
            continue

        # Detect gateway
        gateway = detect_gateway(narration)
        if gateway:
            gateway_counts[gateway] = gateway_counts.get(gateway, 0) + 1

        row_id = f"bank_{bank_name.lower().replace(' ', '_')}_{row_num:05d}"

        rows.append(ParsedBankRow(
            row_id=row_id,
            value_date=parsed_date,
            narration=narration,
            credit_paise=credit_paise,
            bank_name=bank_name,
            gateway=gateway,
            is_credit=is_credit,
        ))

    razorpay_rows = gateway_counts.get("razorpay", 0)
    other_gw = sum(v for k, v in gateway_counts.items() if k != "razorpay")
    unknown = len(rows) - sum(gateway_counts.values())

    return BankParseResult(
        rows=rows,
        bank_name=bank_name,
        total_rows=len(rows),
        credit_rows=sum(1 for r in rows if r.is_credit),
        razorpay_rows=razorpay_rows,
        other_gateway_rows=other_gw,
        unknown_rows=unknown,
        gateway_summary=gateway_counts,
    )
