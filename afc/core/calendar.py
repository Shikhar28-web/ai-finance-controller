"""Working-day arithmetic for the T+2 settlement window. SPEC 4.

A working day is any day that is not a Sunday, not a declared bank holiday, and not
the second or fourth Saturday of its month. First, third and fifth Saturdays are
working days -- that is the Indian banking convention, and getting it wrong in
either direction moves the AWAITING_BANK / UNRESOLVED boundary.

Everything here is a pure function of its arguments. `as_of` is passed in, never
read from the clock: AWAITING_BANK vs UNRESOLVED turns on this window, so a
wall-clock read would silently reclassify a frozen dataset overnight and make the
held-out confusion matrix unreproducible. tools/check_imports.py enforces that.
"""

from __future__ import annotations

from datetime import date, timedelta

SATURDAY = 5
SUNDAY = 6


def saturday_ordinal(day: date) -> int:
    """Which Saturday of its month this is (1-5). Zero if it is not a Saturday."""
    if day.weekday() != SATURDAY:
        return 0
    return (day.day - 1) // 7 + 1


def is_bank_off_saturday(day: date) -> bool:
    """True for the 2nd and 4th Saturday. The 1st, 3rd and 5th are working days."""
    return saturday_ordinal(day) in (2, 4)


def is_working_day(day: date, holidays: frozenset[date]) -> bool:
    if day.weekday() == SUNDAY:
        return False
    if is_bank_off_saturday(day):
        return False
    return day not in holidays


def add_working_days(start: date, n: int, holidays: frozenset[date]) -> date:
    """Advance `n` working days from `start`. n == 0 returns `start` unchanged.

    Counts only working days, so the result is always a working day for n >= 1.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    day = start
    remaining = n
    while remaining > 0:
        day += timedelta(days=1)
        if is_working_day(day, holidays):
            remaining -= 1
    return day


def working_days_between(start: date, end: date, holidays: frozenset[date]) -> int:
    """Working days strictly after `start` up to and including `end`. Negative if end < start."""
    if end < start:
        return -working_days_between(end, start, holidays)
    count = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if is_working_day(day, holidays):
            count += 1
    return count


def settlement_due_date(settled_on: date, t_plus: int, holidays: frozenset[date]) -> date:
    """The working day by which the bank credit is expected. SPEC 4: T+2."""
    return add_working_days(settled_on, t_plus, holidays)


def is_overdue(settled_on: date, as_of: date, t_plus: int, holidays: frozenset[date]) -> bool:
    """True once `as_of` is past the T+N window -- the AWAITING_BANK / UNRESOLVED line.

    A settlement is *not* overdue on its due date, only after it. `as_of` is an
    argument, never a clock read.
    """
    return as_of > settlement_due_date(settled_on, t_plus, holidays)
