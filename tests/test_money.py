"""Integer-paise arithmetic. SPEC 4, SPEC 12."""

import pytest

from afc.money import (
    fee_paise,
    format_rupees,
    gst_on_fee_paise,
    net_per_payment_paise,
    parse_paise,
    round_half_away_from_zero,
)


def test_the_bankers_rounding_trap_that_spec4_round_would_have_hit():
    # 1500 * 0.019 is EXACTLY 28.5 in binary64 -- no representation error involved.
    # Python's round() is banker's rounding, so it ties to even and returns 28.
    # Finance rounds halves away from zero: 29.
    assert 1500 * 0.019 == 28.5
    assert round(1500 * 0.019) == 28
    assert fee_paise(1500, 190) == 29


def test_the_same_bug_hides_itself_at_a_different_rate():
    # At 2.1% the float lands at 31.500000000000004 rather than the true tie 31.5,
    # so round() happens to give the right answer. The defect is rate-dependent,
    # which is why integer bps is the fix rather than "use round() carefully".
    assert 1500 * 0.021 != 31.5
    assert round(1500 * 0.021) == 32
    assert fee_paise(1500, 210) == 32


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [(285000, 10000, 29), (-285000, 10000, -29), (5, 10, 1), (-5, 10, -1), (4, 10, 0), (0, 7, 0)],
)
def test_halves_round_away_from_zero_symmetrically(numerator, denominator, expected):
    assert round_half_away_from_zero(numerator, denominator) == expected


def test_gst_is_charged_on_the_fee_not_the_transaction():
    fee = fee_paise(100_000, 190)
    assert fee == 1_900
    assert gst_on_fee_paise(fee, 1_800) == 342
    assert net_per_payment_paise(100_000, 190, 1_800) == 100_000 - 1_900 - 342


def test_net_is_gross_minus_fee_minus_gst():
    gross, mdr, gst = 123_457, 190, 1_800
    fee = fee_paise(gross, mdr)
    assert net_per_payment_paise(gross, mdr, gst) == gross - fee - gst_on_fee_paise(fee, gst)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1234.56", 123456), ("1,234.56", 123456), ("(1,234.56)", -123456), ("-1234.5", -123450),
     ("1234", 123400), ("0.07", 7), ("Rs 1,00,000.00".replace("Rs ", ""), 10000000)],
)
def test_money_strings_parse_without_touching_float(text, expected):
    assert parse_paise(text) == expected


@pytest.mark.parametrize("text", ["12.345", "abc", "", "1.2.3"])
def test_ambiguous_money_strings_are_refused_rather_than_guessed(text):
    with pytest.raises(ValueError):
        parse_paise(text)


@pytest.mark.parametrize("bad", [1500.0, "1500", None])
def test_floats_and_strings_are_rejected_at_the_arithmetic_boundary(bad):
    with pytest.raises(TypeError):
        fee_paise(bad, 190)


def test_format_is_display_only():
    assert format_rupees(98_741_200) == "Rs 987,412.00"
    assert format_rupees(-39_212) == "-Rs 392.12"
    assert format_rupees(7) == "Rs 0.07"
