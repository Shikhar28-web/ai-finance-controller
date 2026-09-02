"""Invariants on the answer key. These guard the measurement, not the engine."""

import pytest

from afc.config import MATERIALITY_PAISE, STATES
from afc.core.faults import (
    BY_MAGNITUDE,
    CLEAN_STATE,
    COMPOUND_PAIRS,
    DEV_ONLY_PAIRS,
    SINGLE_FAULTS,
    axis_of,
    is_cross_axis,
    state_for_bank_amount_mismatch,
)


def test_the_ten_approved_faults_and_no_others():
    assert set(SINGLE_FAULTS) == {
        "fee_rate_drift", "missing_utr", "bank_credit_missing", "bank_amount_mismatch",
        "refund_not_in_ledger", "duplicate_ledger_entry", "duplicate_bank_credit",
        "orphan_bank_credit", "timing_lag", "timing_overdue",
    }


@pytest.mark.parametrize("name", sorted(SINGLE_FAULTS))
def test_every_fault_declares_an_axis_and_a_reason(name):
    spec = SINGLE_FAULTS[name]
    assert spec.axis in {"gap", "ledger", "presence", "keying", "none"}
    assert len(spec.reason) > 40, "a one-line reason, not a label"


@pytest.mark.parametrize("name", sorted(SINGLE_FAULTS))
def test_every_true_state_is_a_spec7_state_or_the_magnitude_sentinel(name):
    true_state = SINGLE_FAULTS[name].true_state
    assert true_state in STATES or true_state == BY_MAGNITUDE


def test_all_five_states_are_reachable_from_the_mapping():
    # A state no fault can produce is an empty row in the confusion matrix.
    reachable = {CLEAN_STATE}
    for spec in SINGLE_FAULTS.values():
        if spec.true_state != BY_MAGNITUDE:
            reachable.add(spec.true_state)
    reachable |= {state_for_bank_amount_mismatch(n) for n in (1, MATERIALITY_PAISE)}
    assert reachable == set(STATES)


# ---------------------------------------------------------------- composition rule
@pytest.mark.parametrize("pair", sorted(COMPOUND_PAIRS))
def test_held_out_compound_pairs_are_cross_axis(pair):
    # Same-axis pairs interact through attribution, so their true state would depend
    # on decomposer behaviour -- which an answer key may not do.
    assert is_cross_axis(pair), f"{pair} share axis {axis_of(pair[0])}"


@pytest.mark.parametrize("pair", sorted(DEV_ONLY_PAIRS))
def test_dev_only_pairs_are_same_axis_and_carry_a_reason(pair):
    assert not is_cross_axis(pair)
    assert len(DEV_ONLY_PAIRS[pair]) > 60


def test_the_owners_predicted_pair_is_excluded_from_held_out():
    pair = ("fee_rate_drift", "duplicate_bank_credit")
    assert pair in DEV_ONLY_PAIRS
    assert pair not in COMPOUND_PAIRS


@pytest.mark.parametrize("pair", sorted(COMPOUND_PAIRS) + sorted(DEV_ONLY_PAIRS))
def test_compound_pairs_only_reference_known_compoundable_faults(pair):
    for name in pair:
        assert name in SINGLE_FAULTS
        assert SINGLE_FAULTS[name].compoundable, f"{name} has no counterpart to compound onto"


def test_no_pair_is_listed_as_both_held_out_and_dev_only():
    assert not set(COMPOUND_PAIRS) & set(DEV_ONLY_PAIRS)


@pytest.mark.parametrize("pair", sorted(COMPOUND_PAIRS))
def test_every_compound_true_state_is_a_spec7_state_with_a_reason(pair):
    spec = COMPOUND_PAIRS[pair]
    assert spec.true_state in STATES
    assert len(spec.reason) > 40


# ---------------------------------------------------------------- magnitude split
def test_material_mismatches_are_unresolved_and_bounded_ones_are_human_review():
    assert state_for_bank_amount_mismatch(MATERIALITY_PAISE) == "UNRESOLVED"
    assert state_for_bank_amount_mismatch(MATERIALITY_PAISE - 1) == "HUMAN_REVIEW"
    assert state_for_bank_amount_mismatch(-MATERIALITY_PAISE) == "UNRESOLVED"
    assert state_for_bank_amount_mismatch(1) == "HUMAN_REVIEW"


def test_the_magnitude_sentinel_is_used_by_exactly_the_fault_that_has_a_rule():
    by_magnitude = {n for n, s in SINGLE_FAULTS.items() if s.true_state == BY_MAGNITUDE}
    assert by_magnitude == {"bank_amount_mismatch"}
