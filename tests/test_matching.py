"""Levels 1 and 3. SPEC 5.

Expectations are derived from ground truth, never written as literals.
"""

import random
from collections import Counter
from dataclasses import replace
from datetime import date

import pytest

from afc.config import SEED_DEV, SEED_SEALED_EVAL, TOLERANCE_PAISE
from afc.core import integrity, matching
from afc.core.models import BankRow, Settlement
from afc.generate.generator import generate
from afc.ingest.narration import extract_utr

SEEDS = [SEED_DEV, SEED_SEALED_EVAL]
SETTLEMENT_FAULTS = {
    "missing_utr", "bank_credit_missing", "timing_lag", "timing_overdue",
    "duplicate_bank_credit", "fee_rate_drift", "bank_amount_mismatch",
}


def build(seed):
    ds = generate(seed)
    umap = {b.bank_row_id: extract_utr(b.narration) for b in ds.bank_rows}
    rep = integrity.run(ds.ledger, ds.bank_rows, umap)
    m = matching.run(ds.payments, ds.settlements, ds.ledger, ds.refunds,
                     ds.bank_rows, umap, rep, ds.holidays)
    return ds, umap, rep, m


def settlement_faults(ds) -> dict[str, set[str]]:
    truth = {t.unit_id: t for t in ds.truth}
    pay_settlement = {p.payment_id: p.settlement_id for p in ds.payments}
    out: dict[str, set[str]] = {}
    for p in ds.payments:
        for f in truth[p.payment_id].faults:
            if f in SETTLEMENT_FAULTS:
                out.setdefault(pay_settlement[p.payment_id], set()).add(f)
    return out


# ----------------------------------------------------------------- Level 3 outcomes
@pytest.mark.parametrize("seed", SEEDS)
def test_level3_outcomes_match_ground_truth(seed):
    ds, _, _, m = build(seed)
    faults = settlement_faults(ds)
    expected = Counter()
    for s in ds.settlements:
        f = faults.get(s.settlement_id, set())
        if f & {"bank_credit_missing", "timing_lag", "timing_overdue"}:
            expected[matching.UNMATCHED_NO_BANK_ROW] += 1
        elif "missing_utr" in f:
            expected[matching.INFERRED] += 1
        else:
            expected[matching.KEYED] += 1
    assert m.outcome_counts() == dict(sorted(expected.items()))


@pytest.mark.parametrize("seed", SEEDS)
def test_orphan_bank_rows_match_the_injected_count(seed):
    ds, _, _, m = build(seed)
    expected = {t.unit_id for t in ds.truth if t.fault_class == "orphan_bank_credit"}
    assert set(m.orphan_bank_row_ids) == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_a_clean_keyed_settlement_has_a_gap_of_exactly_zero(seed):
    ds, _, _, m = build(seed)
    faults = settlement_faults(ds)
    checked = 0
    for r in m.level3:
        if r.outcome != matching.KEYED or faults.get(r.settlement_id):
            continue
        assert r.gap_paise == 0, f"{r.settlement_id} gap {r.gap_paise}"
        assert abs(r.gap_paise) <= TOLERANCE_PAISE
        checked += 1
    assert checked >= 5


# ----------------------------------------------------------------- Level 1
@pytest.mark.parametrize("seed", SEEDS)
def test_level1_covers_every_payment_and_partitions_cleanly(seed):
    ds, _, _, m = build(seed)
    assert len(m.level1) == len(ds.payments)
    agrees = sum(r.ledger_agrees for r in m.level1)
    dupes = sum(r.ledger_duplicate for r in m.level1)
    disagrees = sum(not r.ledger_agrees and not r.ledger_duplicate for r in m.level1)
    assert agrees + dupes + disagrees == len(ds.payments)


@pytest.mark.parametrize("seed", SEEDS)
def test_ledger_disagreements_are_exactly_the_refund_not_in_ledger_units(seed):
    ds, _, _, m = build(seed)
    expected = {t.unit_id for t in ds.truth if "refund_not_in_ledger" in t.faults}
    actual = {
        r.payment_id for r in m.level1 if not r.ledger_agrees and not r.ledger_duplicate
    }
    assert actual == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_fee_drift_does_not_register_as_a_ledger_disagreement(seed):
    # The merchant books what reached the bank, so fee drift is a gap-axis fault only.
    # Counting it on both axes would double-count one injected fault.
    ds, _, _, m = build(seed)
    drifted = {t.unit_id for t in ds.truth if t.faults == ("fee_rate_drift",)}
    assert drifted
    for r in m.level1:
        if r.payment_id in drifted:
            assert r.ledger_agrees


# ------------------------------------------- Level 1 consumes Level 0, asserted
def test_level1_follows_a_mutated_level0_finding_rather_than_re_deriving():
    ds, _, rep, _ = build(SEED_DEV)
    victim = next(r.payment_id for r in matching.match_level1(
        ds.payments, ds.ledger, ds.refunds, rep) if r.ledger_agrees)

    extra = replace(rep, ledger_duplicates=rep.ledger_duplicates + (
        integrity.DuplicateFinding(
            axis=integrity.LEDGER, kind=integrity.DUPLICATE_LEDGER_ENTRY,
            key=victim, row_ids=("x", "y"), excess_paise=1),))
    after = {r.payment_id: r for r in matching.match_level1(
        ds.payments, ds.ledger, ds.refunds, extra)}
    assert after[victim].ledger_duplicate is True
    assert after[victim].ledger_agrees is False

    fewer = replace(rep, ledger_duplicates=rep.ledger_duplicates[1:])
    dropped = rep.ledger_duplicates[0].key
    after2 = {r.payment_id: r for r in matching.match_level1(
        ds.payments, ds.ledger, ds.refunds, fewer)}
    assert after2[dropped].ledger_duplicate is False


# ----------------------------------------------------------------- tie-break policy
def _stub(expected_paise, n_candidates):
    day = date(2026, 6, 10)
    s = Settlement("setl_x", date(2026, 6, 8), None, day)
    rows = [
        BankRow(f"bank_{i}", day, "NEFT/NA/RAZORPAY SETTLEMENT", expected_paise)
        for i in range(n_candidates)
    ]
    return s, rows


def test_exactly_one_candidate_gives_an_inferred_match():
    s, rows = _stub(0, 1)
    res, _ = matching.match_level3([s], [], [], rows,
                                   {r.bank_row_id: None for r in rows},
                                   integrity.IntegrityReport((), (), ()), frozenset())
    assert res[0].outcome == matching.INFERRED
    assert res[0].candidates_considered == 1


def test_two_candidates_resolve_to_unmatched_never_a_pick():
    # No nearest-neighbour, no first-in-file, no lowest-id: any of those would make
    # the result depend on row order and sealed_eval irreproducible.
    s, rows = _stub(0, 2)
    res, _ = matching.match_level3([s], [], [], rows,
                                   {r.bank_row_id: None for r in rows},
                                   integrity.IntegrityReport((), (), ()), frozenset())
    assert res[0].outcome == matching.UNMATCHED_AMBIGUOUS
    assert res[0].candidates_considered == 2
    assert res[0].bank_row_ids == ()
    assert res[0].actual_paise is None


def test_no_candidate_is_distinguished_from_ambiguous():
    s, rows = _stub(0, 0)
    res, _ = matching.match_level3([s], [], [], rows, {},
                                   integrity.IntegrityReport((), (), ()), frozenset())
    assert res[0].outcome == matching.UNMATCHED_NO_CANDIDATE


def test_a_candidate_outside_tolerance_is_not_a_candidate():
    day = date(2026, 6, 10)
    s = Settlement("setl_x", date(2026, 6, 8), None, day)
    row = BankRow("bank_0", day, "NEFT/NA/X", TOLERANCE_PAISE + 1)
    res, _ = matching.match_level3([s], [], [], [row], {"bank_0": None},
                                   integrity.IntegrityReport((), (), ()), frozenset())
    assert res[0].outcome == matching.UNMATCHED_NO_CANDIDATE


# ----------------------------------------------------------------- reproducibility
@pytest.mark.parametrize("seed", SEEDS)
def test_matching_is_independent_of_input_row_order(seed):
    ds, umap, rep, baseline = build(seed)
    rng = random.Random(99)
    rows, pays, ledger = list(ds.bank_rows), list(ds.payments), list(ds.ledger)
    rng.shuffle(rows)
    rng.shuffle(pays)
    rng.shuffle(ledger)
    rep2 = integrity.run(ledger, rows, umap)
    shuffled = matching.run(pays, ds.settlements, ledger, ds.refunds, rows, umap,
                            rep2, ds.holidays)
    assert shuffled == baseline
