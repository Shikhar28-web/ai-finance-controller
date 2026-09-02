"""Level 0. SPEC 5, SPEC 7 R7/R9.

Expected counts are DERIVED two independent ways -- from the injection plan and from
the generator's ground truth -- and never written as literals. A hardcoded expectation
passes whether or not the logic under test is right.
"""

from collections import Counter

import pytest

from afc.config import SEED_DEV, SEED_SEALED_EVAL
from afc.core import integrity
from afc.core.faults import GAP, LEDGER
from afc.core.models import BankRow, LedgerEntry
from afc.generate import plan
from afc.generate.generator import generate
from afc.ingest.narration import extract_utr

SEEDS = [SEED_DEV, SEED_SEALED_EVAL]


def build(seed, targets=plan.DEFAULT):
    ds = generate(seed, targets)
    utr_map = {b.bank_row_id: extract_utr(b.narration) for b in ds.bank_rows}
    return ds, integrity.run(ds.ledger, ds.bank_rows, utr_map)


def ledger_units_from_truth(ds) -> int:
    return sum(1 for t in ds.truth if "duplicate_ledger_entry" in t.faults)


def bank_settlements_from_truth(ds) -> int:
    pay_settlement = {p.payment_id: p.settlement_id for p in ds.payments}
    return len({
        pay_settlement[t.unit_id]
        for t in ds.truth
        if "duplicate_bank_credit" in t.faults and t.unit_id in pay_settlement
    })


# ------------------------------------------------- the two derivations must agree
@pytest.mark.parametrize("seed", SEEDS)
def test_plan_and_ground_truth_agree_on_the_ledger_duplicate_count(seed):
    ds, _ = build(seed)
    assert plan.expected_ledger_duplicate_units(plan.DEFAULT) == ledger_units_from_truth(ds)


# ------------------------------------------------- counting definitions
@pytest.mark.parametrize("seed", SEEDS)
def test_level0_finds_one_group_per_affected_payment(seed):
    ds, rep = build(seed)
    expected = plan.expected_ledger_duplicate_units(plan.DEFAULT)
    assert len(rep.ledger_duplicates) == expected
    assert rep.flagged_payment_ids == {
        t.unit_id for t in ds.truth if "duplicate_ledger_entry" in t.faults
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_groups_excess_and_members_are_three_different_numbers(seed):
    _, rep = build(seed)
    s = rep.summary()
    # Duplication factor is exactly 2 here, so groups == excess_rows. That coincidence
    # is why an ambiguous count survives review; members is twice both and is NOT a
    # duplicate count. Flagging members would pull clean records into HUMAN_REVIEW via
    # the R7 ledger clause and read like a classifier result rather than a counting bug.
    assert s["ledger_duplicate_groups"] == s["ledger_excess_rows"]
    assert s["ledger_group_members"] == 2 * s["ledger_duplicate_groups"]
    assert len(rep.flagged_payment_ids) == s["ledger_duplicate_groups"]


@pytest.mark.parametrize("seed", SEEDS)
def test_level0_finds_one_group_per_settlement_with_a_duplicated_credit(seed):
    ds, rep = build(seed)
    assert len(rep.bank_duplicates) == bank_settlements_from_truth(ds)


# ------------------------------------------------- the narration trap
@pytest.mark.parametrize("seed", SEEDS)
def test_bank_rows_group_by_utr_not_narration(seed):
    ds, rep = build(seed)
    by_narration = Counter(b.narration for b in ds.bank_rows)
    phantom = {k: v for k, v in by_narration.items() if v > 1 and "/NA/" in k}
    assert phantom, "expected missing_utr settlements to share a narration"
    # Grouping on narration would merge those unrelated settlements into one phantom
    # duplicate group and over-report.
    assert len(rep.bank_duplicates) < len(
        {k for k, v in by_narration.items() if v > 1}
    )
    assert all(not f.key.endswith("NA") for f in rep.bank_duplicates)


@pytest.mark.parametrize("seed", SEEDS)
def test_rows_that_cannot_be_keyed_are_reported_not_silently_dropped(seed):
    ds, rep = build(seed)
    unkeyed = {b.bank_row_id for b in ds.bank_rows if extract_utr(b.narration) is None}
    assert rep.unkeyed_bank_rows and set(rep.unkeyed_bank_rows) == unkeyed
    assert not (set(rep.unkeyed_bank_rows) & {r for f in rep.bank_duplicates for r in f.row_ids})


# ------------------------------------------------- axis routing
@pytest.mark.parametrize("seed", SEEDS)
def test_findings_carry_the_axis_that_selects_the_spec7_clause(seed):
    _, rep = build(seed)
    # An undifferentiated "duplicate found" flag would leave the cascade unable to
    # choose between the R7 ledger clause and the R9 integrity amendment.
    assert {f.axis for f in rep.ledger_duplicates} == {LEDGER}
    assert {f.axis for f in rep.bank_duplicates} == {GAP}
    assert {f.kind for f in rep.ledger_duplicates} == {integrity.DUPLICATE_LEDGER_ENTRY}
    assert {f.kind for f in rep.bank_duplicates} == {integrity.DUPLICATE_BANK_CREDIT}


# ------------------------------------------------- shape and determinism
def test_no_duplicates_means_no_findings():
    ledger = [LedgerEntry(f"l{i}", f"p{i}", 100, None) for i in range(5)]
    rows = [BankRow(f"b{i}", None, f"NEFT/UTR{i:06d}/X", 100) for i in range(5)]
    rep = integrity.run(ledger, rows, {r.bank_row_id: extract_utr(r.narration) for r in rows})
    assert rep.ledger_duplicates == () and rep.bank_duplicates == ()
    assert rep.unkeyed_bank_rows == ()


def test_a_triplicated_payment_is_one_group_with_two_excess_rows():
    ledger = [LedgerEntry(f"l{i}", "p1", 500, None) for i in range(3)]
    (finding,) = integrity.find_ledger_duplicates(ledger)
    assert finding.group_members == 3
    assert finding.excess_rows == 2
    assert finding.excess_paise == 1000


@pytest.mark.parametrize("seed", SEEDS)
def test_findings_are_deterministic_and_ordered(seed):
    _, a = build(seed)
    _, b = build(seed)
    assert a == b
    keys = [f.key for f in a.ledger_duplicates]
    assert keys == sorted(keys)


def test_level0_runs_on_the_shifted_split():
    ds, rep = build(2027, plan.SHIFTED)
    assert len(rep.ledger_duplicates) == ledger_units_from_truth(ds)
    assert len(rep.bank_duplicates) == bank_settlements_from_truth(ds)
