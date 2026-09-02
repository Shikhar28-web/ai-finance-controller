"""Integer-paise money arithmetic. No float ever crosses this boundary.

SPEC 4 writes the settlement arithmetic as `round(gross * mdr_rate)`, which names
neither a rounding mode nor a numeric type. Three plausible readings disagree:

    gross = 1500 paise, MDR 1.9%
        1500 * 0.019            -> exactly 28.5 (28.5 IS representable in binary64)
        round(28.5)             -> 28    Python's round() is banker's: ties to even
        half away from zero     -> 29    what a finance reviewer expects

    gross = 1500 paise, MDR 2.1%
        1500 * 0.021            -> 31.500000000000004, not the true tie 31.5
        round(...)              -> 32    accidentally correct, via representation error

Two distinct mechanisms, and which one bites depends on the rate, so the bug
survives casual testing. Both vanish under integer basis points with an explicit
half-away-from-zero rule, which is also platform-stable. SPEC 12: never floats
for currency.
"""

BPS_DIVISOR = 10_000


def _check_int(value: int, name: str) -> int:
    # bool is an int subclass; float/Decimal are the mistakes worth catching loudly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int paise, got {type(value).__name__}: {value!r}")
    return value


def round_half_away_from_zero(numerator: int, denominator: int) -> int:
    """Exact integer division rounding halves away from zero (the finance convention).

    Pure integer arithmetic: no float, no Decimal, identical on every platform.
    """
    _check_int(numerator, "numerator")
    _check_int(denominator, "denominator")
    if denominator <= 0:
        raise ValueError(f"denominator must be positive, got {denominator}")
    if numerator >= 0:
        return (numerator * 2 + denominator) // (denominator * 2)
    return -((-numerator * 2 + denominator) // (denominator * 2))


def apply_bps(amount_paise: int, bps: int) -> int:
    """amount * bps/10000, rounded half away from zero."""
    _check_int(amount_paise, "amount_paise")
    _check_int(bps, "bps")
    return round_half_away_from_zero(amount_paise * bps, BPS_DIVISOR)


def fee_paise(gross_paise: int, mdr_bps: int) -> int:
    """SPEC 4: fee = round(gross * mdr_rate)."""
    return apply_bps(gross_paise, mdr_bps)


def gst_on_fee_paise(fee: int, gst_bps: int) -> int:
    """SPEC 4: gst_on_fee = round(fee * 0.18). GST is on the fee, not the transaction."""
    return apply_bps(fee, gst_bps)


def net_per_payment_paise(gross_paise: int, mdr_bps: int, gst_bps: int) -> int:
    """SPEC 4: net_per_payment = gross - fee - gst_on_fee.

    GST is computed per payment and then summed, exactly as SPEC 4 writes it --
    not on the summed fee. The two differ by a few paise across a settlement, and
    that difference is what the decomposer's rounding_variance accounts for.
    """
    fee = fee_paise(gross_paise, mdr_bps)
    return gross_paise - fee - gst_on_fee_paise(fee, gst_bps)


def parse_paise(text: str) -> int:
    """Parse a money string from a CSV into integer paise without touching float.

    Accepts '1234.56', '1,234.56', '(1,234.56)' for negative, '-1234.5', '1234'.
    Rejects anything with more than two decimal places rather than guessing.
    """
    s = text.strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not s:
        raise ValueError("empty money string")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith("-"):
        negative, s = not negative, s[1:].strip()
    if s.startswith("+"):
        s = s[1:].strip()
    if "." in s:
        whole, _, frac = s.partition(".")
    else:
        whole, frac = s, ""
    if not whole:
        whole = "0"
    if not whole.isdigit() or (frac and not frac.isdigit()):
        raise ValueError(f"not a money value: {text!r}")
    if len(frac) > 2:
        raise ValueError(f"more than 2 decimal places, refusing to guess: {text!r}")
    paise = int(whole) * 100 + int(frac.ljust(2, "0") or "0")
    return -paise if negative else paise


def format_rupees(paise: int) -> str:
    """Display only. Never feed this back into arithmetic."""
    _check_int(paise, "paise")
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    return f"{sign}Rs {whole:,}.{frac:02d}"
