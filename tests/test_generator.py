"""Generator invariants. SPEC 4, SPEC 10 step 1, SPEC 12."""

from collections import Counter, defaultdict

import pytest

from afc.config import (
    CONTRACTED_MDR_BPS,
    GST_ON_FEE_BPS,
    SEED_DEV,
    SEED_HELDOUT_SEALED,
    SEED_HELDOUT_USED,
    TOLERANCE_PAISE,
)
from afc.core.calendar import is_overdue, is_working_day
from afc.core.faults import DEV_ONLY_PAIRS, LEDGER, axis_of
from afc.core.models import ORPHAN_BANK_CREDIT, PAYMENT
from afc.generate import plan
from afc.generate.generator import T_PLUS, generate
from afc.money import net_per_payment_paise

DEV = generate(SEED_DEV)
HELD = generate(SEED_HELDOUT_USED)
DEV_ONLY_CLASSES = {"+".join(sorted(p)) for p in DEV_ONLY_PAIRS}


def test_five_hundred_orders_and_payments():
    assert len(DEV.orders) == len(DEV.payments) == plan.TARGET_ORDERS


def test_one_recon_unit_per_payment_plus_one_per_orphan_bank_credit():
    kinds = Counter(t.unit_kind for t in DEV.truth)
    assert kinds[PAYMENT] == plan.TARGET_ORDERS
    assert kinds[ORPHAN_BANK_CREDIT] == plan.ORPHAN_BANK_CREDIT_UNITS
    assert len(DEV.truth) == plan.TARGET_ORDERS + plan.ORPHAN_BANK_CREDIT_UNITS


def test_generation_is_reproducible_for_a_fixed_seed():
    a, b = generate(SEED_DEV), generate(SEED_DEV)
    assert a.payments == b.payments
    assert a.bank_rows == b.bank_rows
    assert a.ledger == b.ledger
    assert a.truth == b.truth


def test_dev_and_held_out_differ_by_seed_alone():
    # Same code path, so the class composition is identical; only amounts, refund
    # incidence and settlement dating vary. Recorded as a limitation, not a defect.
    assert Counter(t.fault_class for t in DEV.truth) == Counter(
        t.fault_class for t in HELD.truth
    )
    assert [p.gross_paise for p in DEV.payments] != [p.gross_paise for p in HELD.payments]


@pytest.mark.parametrize("seed", [SEED_DEV, SEED_HELDOUT_USED, *SEED_HELDOUT_SEALED])
def test_every_seed_produces_a_complete_dataset(seed):
    ds = generate(seed)
    assert len(ds.payments) == plan.TARGET_ORDERS
    assert len(ds.truth) == plan.TARGET_ORDERS + plan.ORPHAN_BANK_CREDIT_UNITS


# ----------------------------------------------------------------- money
def test_all_money_is_integer_paise():
    values = (
        [p.gross_paise for p in DEV.payments]
        + [b.credit_paise for b in DEV.bank_rows]
        + [entry.amount_paise for entry in DEV.ledger]
        + [r.amount_paise for r in DEV.refunds]
    )
    assert values and all(isinstance(v, int) and not isinstance(v, bool) for v in values)


def test_a_clean_settlement_reconciles_to_exactly_zero():
    """Recomputed from the emitted data, not from generator internals.

    This is what makes TOLERANCE_PAISE non-binding on synthetic data: a clean
    settlement's gap is exactly 0, not merely within a rupee.
    """
    by_settlement = defaultdict(list)
    for p in DEV.payments:
        by_settlement[p.settlement_id].append(p)
    refunds = defaultdict(int)
    payment_settlement = {p.payment_id: p.settlement_id for p in DEV.payments}
    for r in DEV.refunds:
        refunds[payment_settlement[r.payment_id]] += r.amount_paise
    # Only non-ledger-axis faults disturb the settlement-to-bank arithmetic. A
    # settlement carrying duplicate_ledger_entry or refund_not_in_ledger still
    # reconciles to the bank exactly -- that is precisely what the axis rule says.
    disturbed = {
        t.unit_id
        for t in DEV.truth
        if t.unit_kind == PAYMENT and any(axis_of(f) != LEDGER for f in t.faults)
    }
    credits = defaultdict(int)
    utr_to_settlement = {s.utr: s.settlement_id for s in DEV.settlements if s.utr}
    for b in DEV.bank_rows:
        utr = b.narration.split("/")[1]
        if utr in utr_to_settlement:
            credits[utr_to_settlement[utr]] += b.credit_paise

    checked = 0
    for sid, payments in by_settlement.items():
        if any(p.payment_id in disturbed for p in payments):
            continue
        expected = sum(
            net_per_payment_paise(p.gross_paise, CONTRACTED_MDR_BPS, GST_ON_FEE_BPS)
            for p in payments
        ) - refunds[sid]
        assert credits[sid] - expected == 0, f"{sid} gap {credits[sid] - expected}"
        assert abs(credits[sid] - expected) < TOLERANCE_PAISE
        checked += 1
    assert checked >= 5, f"only {checked} fully clean settlements to check"


def test_fee_drift_settlements_charge_the_drifted_rate_and_others_the_contract():
    drifted = {
        p.settlement_id
        for p in DEV.payments
        if p.applied_mdr_bps == plan.DRIFTED_MDR_BPS
    }
    assert drifted
    for p in DEV.payments:
        expected = plan.DRIFTED_MDR_BPS if p.settlement_id in drifted else CONTRACTED_MDR_BPS
        assert p.applied_mdr_bps == expected


# ----------------------------------------------------------------- faults
def test_missing_utr_settlements_have_no_utr_and_others_do():
    truth_by_payment = {t.unit_id: t for t in DEV.truth if t.unit_kind == PAYMENT}
    missing = {
        p.settlement_id
        for p in DEV.payments
        if "missing_utr" in truth_by_payment[p.payment_id].faults
    }
    assert missing
    for s in DEV.settlements:
        assert (s.utr is None) == (s.settlement_id in missing)


def test_duplicate_ledger_entries_are_actually_duplicated():
    counts = Counter(e.payment_id for e in DEV.ledger)
    truth = {t.unit_id: t for t in DEV.truth if t.unit_kind == PAYMENT}
    for pid, n in counts.items():
        assert n == (2 if "duplicate_ledger_entry" in truth[pid].faults else 1)


def test_timing_lag_settlements_are_inside_the_window_and_overdue_ones_are_past():
    truth = {t.unit_id: t for t in DEV.truth if t.unit_kind == PAYMENT}
    kind = {}
    for p in DEV.payments:
        for f in ("timing_lag", "timing_overdue"):
            if f in truth[p.payment_id].faults:
                kind[p.settlement_id] = f
    assert set(kind.values()) == {"timing_lag", "timing_overdue"}
    for s in DEV.settlements:
        if s.settlement_id in kind:
            overdue = is_overdue(s.settled_on, DEV.as_of, T_PLUS, DEV.holidays)
            assert overdue == (kind[s.settlement_id] == "timing_overdue")


def test_orphan_bank_credits_match_no_settlement():
    utrs = {s.utr for s in DEV.settlements if s.utr}
    orphans = {t.unit_id for t in DEV.truth if t.unit_kind == ORPHAN_BANK_CREDIT}
    for b in DEV.bank_rows:
        if b.bank_row_id in orphans:
            assert b.narration.split("/")[1] not in utrs


# ----------------------------------------------------------------- integrity
def test_every_settlement_falls_on_a_working_day():
    for s in DEV.settlements:
        assert is_working_day(s.settled_on, DEV.holidays)
        assert is_working_day(s.due_on, DEV.holidays)


def test_identifiers_are_unique():
    for rows, key in (
        (DEV.payments, "payment_id"), (DEV.orders, "order_id"),
        (DEV.settlements, "settlement_id"), (DEV.bank_rows, "bank_row_id"),
        (DEV.ledger, "ledger_id"), (DEV.refunds, "refund_id"),
    ):
        ids = [getattr(r, key) for r in rows]
        assert len(ids) == len(set(ids)), key
    unit_ids = [t.unit_id for t in DEV.truth]
    assert len(unit_ids) == len(set(unit_ids))


def test_every_held_out_scored_class_clears_the_floor():
    counts = Counter(
        t.fault_class for t in HELD.truth if t.fault_class not in DEV_ONLY_CLASSES
    )
    short = {k: v for k, v in counts.items() if k != "clean" and v < plan.MIN_HELD_OUT_UNITS}
    assert not short, f"under the {plan.MIN_HELD_OUT_UNITS} unit floor: {short}"


def test_same_axis_pairs_are_generated_in_both_splits_for_scoring_time_exclusion():
    # The owner required identical generation AND that these pairs be kept for study,
    # which is only consistent if the exclusion happens at scoring time.
    dev_c = Counter(t.fault_class for t in DEV.truth)
    held_c = Counter(t.fault_class for t in HELD.truth)
    assert DEV_ONLY_CLASSES
    for name in DEV_ONLY_CLASSES:
        assert dev_c[name] > 0 and held_c[name] == dev_c[name]
