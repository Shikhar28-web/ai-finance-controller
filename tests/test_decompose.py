"""Decomposer and attribution scoring. SPEC 6, SPEC 9 addition B."""

import random

import pytest

from afc.config import SEED_DEV, SEED_SEALED_EVAL
from afc.core import decompose as D
from afc.core import integrity, matching
from afc.generate.generator import generate
from afc.ingest.narration import extract_utr
from afc.metrics import attribution

SEEDS = [SEED_DEV, SEED_SEALED_EVAL]
KNOWN_CAUSES = {D.FEE_RATE_DRIFT, D.DUPLICATE_BANK_CREDIT, D.REFUND_VARIANCE,
                D.ADJUSTMENT_VARIANCE, D.ROUNDING}


def pipeline(seed, payments=None):
    ds = generate(seed)
    pays = payments if payments is not None else ds.payments
    umap = {b.bank_row_id: extract_utr(b.narration) for b in ds.bank_rows}
    rep = integrity.run(ds.ledger, ds.bank_rows, umap)
    m = matching.run(pays, ds.settlements, ds.ledger, ds.refunds, ds.bank_rows,
                     umap, rep, ds.holidays)
    decs = D.decompose_all(m.level3, pays, ds.refunds, ds.bank_rows, ds.rate_card)
    return ds, m, decs


# ----------------------------------------------------------------- closure
@pytest.mark.parametrize("seed", SEEDS)
def test_closure_holds_for_every_settlement(seed):
    _, _, decs = pipeline(seed)
    for d in decs:
        assert not d.failed
        assert sum(a.amount_paise for a in d.attributed) + d.unexplained_paise == d.gap_paise


@pytest.mark.parametrize("seed", SEEDS)
def test_ground_truth_itself_closes(seed):
    ds, _, _ = pipeline(seed)
    for t in ds.settlement_truth:
        assert (sum(v for _, v in t.expected_attribution) + t.expected_unexplained_paise
                == t.expected_gap_paise)


def test_a_closure_failure_marks_one_record_and_spares_the_batch(monkeypatch):
    ds = generate(SEED_DEV)
    umap = {b.bank_row_id: extract_utr(b.narration) for b in ds.bank_rows}
    rep = integrity.run(ds.ledger, ds.bank_rows, umap)
    m = matching.run(ds.payments, ds.settlements, ds.ledger, ds.refunds,
                     ds.bank_rows, umap, rep, ds.holidays)
    victim = m.level3[0].settlement_id
    real = D.decompose

    def flaky(result, *a, **kw):
        if result.settlement_id == victim:
            raise D.ClosureError("forced")
        return real(result, *a, **kw)

    monkeypatch.setattr(D, "decompose", flaky)
    decs = D.decompose_all(m.level3, ds.payments, ds.refunds, ds.bank_rows, ds.rate_card)
    failed = [d for d in decs if d.failed]
    assert len(failed) == 1 and failed[0].settlement_id == victim
    assert failed[0].failure_reason == "forced"
    assert len(decs) == len(m.level3), "the rest of the batch still produced results"


# ----------------------------------------------------------------- attribution shape
@pytest.mark.parametrize("seed", SEEDS)
def test_no_cause_is_ever_invented(seed):
    _, _, decs = pipeline(seed)
    for d in decs:
        assert d.cause_set <= KNOWN_CAUSES
        assert all(a.amount_paise != 0 for a in d.attributed), "zero causes are not named"
        assert all(a.proof for a in d.attributed)


@pytest.mark.parametrize("seed", SEEDS)
def test_fee_drift_is_attributed_against_the_contracted_baseline(seed):
    ds, _, decs = pipeline(seed)
    truth = {t.settlement_id: t for t in ds.settlement_truth}
    checked = 0
    for d in decs:
        t = truth.get(d.settlement_id)
        if t is None or "fee_rate_drift" not in t.gap_faults:
            continue
        got = {a.cause: a.amount_paise for a in d.attributed}
        assert got[D.FEE_RATE_DRIFT] == dict(t.expected_attribution)["fee_rate_drift"]
        assert got[D.FEE_RATE_DRIFT] < 0, "a higher applied rate leaves the merchant short"
        checked += 1
    assert checked


@pytest.mark.parametrize("seed", SEEDS)
def test_an_unattributable_gap_stays_unexplained(seed):
    ds, _, decs = pipeline(seed)
    truth = {t.settlement_id: t for t in ds.settlement_truth}
    checked = 0
    for d in decs:
        t = truth.get(d.settlement_id)
        if t is None or "bank_amount_mismatch" not in t.gap_faults:
            continue
        assert d.unexplained_paise == t.expected_unexplained_paise != 0
        checked += 1
    assert checked


def test_a_residual_beyond_the_rounding_bound_is_not_called_rounding():
    assert D.rounding_cap_paise(10) == 10  # 1.09 paise x 10, floored
    assert D.rounding_cap_paise(100) == 109


@pytest.mark.parametrize("seed", SEEDS)
def test_settlements_with_no_bank_credit_have_nothing_to_decompose(seed):
    _, m, decs = pipeline(seed)
    unmatched = {r.settlement_id for r in m.level3 if r.actual_paise is None}
    assert unmatched
    for d in decs:
        if d.settlement_id in unmatched:
            assert d.attributed == () and d.gap_paise == 0 and d.actual_paise is None


@pytest.mark.parametrize("seed", SEEDS)
def test_decomposition_is_independent_of_payment_order(seed):
    ds = generate(seed)
    shuffled = list(ds.payments)
    random.Random(7).shuffle(shuffled)
    _, _, a = pipeline(seed)
    _, _, b = pipeline(seed, payments=shuffled)
    assert {d.settlement_id: d for d in a} == {d.settlement_id: d for d in b}


# ----------------------------------------------------------------- scoring
@pytest.mark.parametrize("seed", SEEDS)
def test_attribution_scores_perfectly_which_is_a_construction_artifact(seed):
    ds, _, decs = pipeline(seed)
    sc = attribution.score(decs, ds.settlement_truth)
    # 1.0 here is coherence, not capability: the generator and the decomposer share
    # the fee arithmetic and the cause list is closed. Asserted so a regression is
    # visible, and disclosed in the README so the number is not read as skill.
    assert sc.exact_cause_set_match_rate == 1.0
    assert sc.rupee_attribution_error_paise == 0
    assert sc.decomposition_failures == 0
    assert sc.settlements_scored == len(ds.settlement_truth)


def test_a_wrong_cause_set_lowers_the_match_rate():
    ds, _, decs = pipeline(SEED_DEV)
    truth = {t.settlement_id: t for t in ds.settlement_truth}
    target = next(d for d in decs if d.settlement_id in truth and d.attributed)
    from dataclasses import replace
    corrupted = tuple(
        replace(d, attributed=d.attributed + (
            D.Attribution(D.ADJUSTMENT_VARIANCE, 1, "phantom"),))
        if d.settlement_id == target.settlement_id else d
        for d in decs
    )
    sc = attribution.score(corrupted, ds.settlement_truth)
    assert sc.exact_cause_set_match_rate < 1.0
    assert sc.rupee_attribution_error_paise > 0
    assert sc.disagreements


def test_a_failed_decomposition_counts_as_error_not_as_a_match():
    ds, _, decs = pipeline(SEED_DEV)
    truth = {t.settlement_id: t for t in ds.settlement_truth}
    from dataclasses import replace
    victim = next(t.settlement_id for t in ds.settlement_truth if t.expected_gap_paise)
    corrupted = tuple(
        replace(d, failed=True, failure_reason="forced", attributed=())
        if d.settlement_id == victim else d for d in decs
    )
    sc = attribution.score(corrupted, ds.settlement_truth)
    assert sc.decomposition_failures == 1
    assert sc.rupee_attribution_error_paise == abs(truth[victim].expected_gap_paise)
    assert sc.exact_cause_set_match_rate < 1.0
