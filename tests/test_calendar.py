"""Working-day calendar. SPEC 4.

The two errors this must not make, in either direction:
  - flagging a settlement overdue before the bank has had T+2 working days
    (false positive on UNRESOLVED -- we accuse a bank that is simply shut)
  - pushing the due date late by treating every Saturday as a bank holiday
    (false negative on UNRESOLVED -- money silently not chased)
"""

from datetime import date, timedelta

import pytest

from afc.core.calendar import (
    add_working_days,
    is_bank_off_saturday,
    is_overdue,
    is_working_day,
    saturday_ordinal,
    settlement_due_date,
    working_days_between,
)
from afc.core.holidays import fixed_national_holidays, holiday_set

H = holiday_set(2026)
T = 2
NO_HOLIDAYS: frozenset[date] = frozenset()


# ----------------------------------------------------------------- Saturdays
@pytest.mark.parametrize(
    ("day", "ordinal"),
    [(date(2026, 1, 3), 1), (date(2026, 1, 10), 2), (date(2026, 1, 17), 3),
     (date(2026, 1, 24), 4), (date(2026, 1, 31), 5)],
)
def test_saturday_ordinal_counts_saturdays_within_the_month(day, ordinal):
    assert saturday_ordinal(day) == ordinal


def test_non_saturdays_have_no_ordinal():
    assert saturday_ordinal(date(2026, 1, 5)) == 0


@pytest.mark.parametrize(
    ("day", "off"),
    [(date(2026, 1, 3), False), (date(2026, 1, 10), True), (date(2026, 1, 17), False),
     (date(2026, 1, 24), True), (date(2026, 1, 31), False)],
)
def test_only_the_second_and_fourth_saturday_are_bank_off(day, off):
    assert is_bank_off_saturday(day) is off


def test_the_fifth_saturday_is_a_working_day():
    # Easy to get wrong with a "day 22-28 is the 4th" style rule.
    fifth = date(2026, 1, 31)
    assert saturday_ordinal(fifth) == 5
    assert is_working_day(fifth, H)


# ----------------------------------------------------------------- working days
def test_sundays_are_never_working_days():
    assert not is_working_day(date(2026, 1, 4), NO_HOLIDAYS)


def test_declared_holidays_are_not_working_days():
    assert not is_working_day(date(2026, 1, 26), H)      # Republic Day
    assert is_working_day(date(2026, 1, 26), NO_HOLIDAYS)  # ...only if declared


def test_ordinary_weekdays_are_working_days():
    assert is_working_day(date(2026, 1, 6), H)


# ------------------------------------------------- the false positive (CASE 1)
@pytest.mark.parametrize(
    "friday",
    [date(2026, 1, 2), date(2026, 1, 9), date(2026, 1, 16),
     date(2026, 1, 23), date(2026, 1, 30)],
)
def test_a_friday_settlement_is_not_overdue_on_the_following_saturday(friday):
    saturday = friday + timedelta(days=1)
    assert saturday.weekday() == 5
    assert not is_overdue(friday, saturday, T, H)


@pytest.mark.parametrize(
    "friday", [date(2026, 1, 2), date(2026, 1, 9), date(2026, 1, 16), date(2026, 1, 30)]
)
def test_a_friday_settlement_is_not_overdue_on_the_following_monday_either(friday):
    # Where naive T+2-calendar-days breaks: by Monday it says overdue, though the
    # bank has had at most one working day.
    monday = friday + timedelta(days=3)
    assert monday.weekday() == 0
    assert monday <= settlement_due_date(friday, T, H)
    assert not is_overdue(friday, monday, T, H)


# ------------------------------------------------- the false negative (CASE 2)
@pytest.mark.parametrize(
    ("settled", "due"),
    [(date(2026, 1, 1), date(2026, 1, 3)),    # Thu -> 1st Sat counts as working
     (date(2026, 1, 15), date(2026, 1, 17)),  # Thu -> 3rd Sat counts
     (date(2026, 1, 29), date(2026, 1, 31)),  # Thu -> 5th Sat counts
     (date(2026, 1, 8), date(2026, 1, 12)),   # Thu -> 2nd Sat skipped, lands Mon
     (date(2026, 1, 22), date(2026, 1, 27))],  # Thu -> 4th Sat + Republic Day
)
def test_working_saturdays_are_counted_so_due_dates_do_not_drift_late(settled, due):
    assert settlement_due_date(settled, T, H) == due


# ----------------------------------------------------------------- arithmetic
def test_zero_working_days_is_the_identity():
    assert add_working_days(date(2026, 1, 6), 0, H) == date(2026, 1, 6)


def test_negative_working_days_is_refused():
    with pytest.raises(ValueError):
        add_working_days(date(2026, 1, 6), -1, H)


def test_advancing_always_lands_on_a_working_day():
    day = date(2026, 1, 1)
    while day < date(2027, 1, 1):
        for n in (1, 2, 3):
            assert is_working_day(add_working_days(day, n, H), H)
        day += timedelta(days=1)


def test_a_holiday_inside_the_window_pushes_the_due_date_out():
    settled = date(2026, 1, 23)
    assert date(2026, 1, 26) in fixed_national_holidays(2026)
    assert settlement_due_date(settled, T, H) == date(2026, 1, 28)
    assert settlement_due_date(settled, T, NO_HOLIDAYS) == date(2026, 1, 27)


def test_working_days_between_is_antisymmetric():
    a, b = date(2026, 1, 1), date(2026, 1, 31)
    assert working_days_between(a, b, H) == -working_days_between(b, a, H)


def test_working_days_between_agrees_with_add_working_days():
    start = date(2026, 2, 2)
    for n in range(0, 15):
        assert working_days_between(start, add_working_days(start, n, H), H) == n


# ----------------------------------------------------------------- boundary
def test_a_settlement_is_not_overdue_on_its_due_date_only_after_it():
    settled = date(2026, 2, 2)
    due = settlement_due_date(settled, T, H)
    assert not is_overdue(settled, due, T, H)
    assert is_overdue(settled, due + timedelta(days=1), T, H)


def test_as_of_is_an_argument_so_the_answer_never_depends_on_today():
    settled, as_of = date(2026, 2, 2), date(2026, 2, 4)
    assert is_overdue(settled, as_of, T, H) == is_overdue(settled, as_of, T, H)
    assert not is_overdue(settled, as_of, T, H)
