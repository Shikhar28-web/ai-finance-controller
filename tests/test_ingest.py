"""Normalization and UTR extraction. SPEC 10 step 2."""

import re

import pytest

from afc.config import SEED_DEV, SEED_SEALED_EVAL
from afc.generate import emit, plan
from afc.generate.generator import generate
from afc.ingest import narration as N
from afc.ingest.loader import load


@pytest.fixture(scope="module")
def dev_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("dev")
    emit.emit(generate(SEED_DEV), out)
    return out


# ----------------------------------------------------------------- round trip
@pytest.mark.parametrize("seed", [SEED_DEV, SEED_SEALED_EVAL])
def test_csv_round_trip_is_exact(seed, tmp_path):
    ds = generate(seed)
    emit.emit(ds, tmp_path)
    b = load(tmp_path)
    assert b.payments == ds.payments
    assert b.settlements == ds.settlements
    assert b.bank_rows == ds.bank_rows
    assert b.ledger == ds.ledger
    assert b.refunds == ds.refunds
    assert b.as_of == ds.as_of
    assert b.holidays == ds.holidays


def test_money_survives_the_rupee_string_format_as_integer_paise(dev_dir):
    b = load(dev_dir)
    values = (
        [p.gross_paise for p in b.payments]
        + [x.credit_paise for x in b.bank_rows]
        + [e.amount_paise for e in b.ledger]
    )
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in values)


def test_the_shifted_split_normalises_too(tmp_path):
    ds = generate(2027, plan.SHIFTED)
    emit.emit(ds, tmp_path, targets_label=plan.SHIFTED.label)
    assert load(tmp_path).payments == ds.payments


# ----------------------------------------------------------------- UTR extraction
@pytest.mark.parametrize(
    ("text", "expected"),
    [("NEFT/UTR42000001/RAZORPAY SETTLEMENT", "UTR42000001"),
     ("neft/utr42000001/razorpay", "UTR42000001"),
     ("NEFT/UTR42ORPH0000/UNKNOWN CREDIT", "UTR42ORPH0000"),
     ("NEFT/NA/RAZORPAY SETTLEMENT", None),
     ("XXUTR123456YY", None),
     ("NEFT/UTR111111/UTR222222/X", None)],
)
def test_extraction_is_exact_or_absent_never_approximate(text, expected):
    assert N.extract_utr(text) == expected


def test_a_coverage_shortfall_raises_rather_than_returning_blanks(dev_dir):
    good = N.UTR_PATTERN
    # The realistic mistake: assume the reference is digits after the prefix. True of
    # settlement UTRs, false of orphan-credit references, so most rows still match.
    N.UTR_PATTERN = re.compile(r"(?:^|[/\s|:-])(UTR[0-9]{6,})(?=$|[/\s|:-])")
    try:
        with pytest.raises(N.NarrationCoverageError, match="indistinguishable"):
            load(dev_dir)
    finally:
        N.UTR_PATTERN = good


def test_over_matching_also_raises(dev_dir):
    good = N.UTR_PATTERN
    N.UTR_PATTERN = re.compile(r"(?:^|[/\s|:-])([A-Z0-9]{2,})(?=$|[/\s|:-])")
    try:
        with pytest.raises(N.NarrationCoverageError):
            load(dev_dir)
    finally:
        N.UTR_PATTERN = good


def test_extract_all_is_silent_when_coverage_is_exact():
    rows = ["NEFT/UTR111111/X", "NEFT/NA/X", "NEFT/UTR222222/X"]
    out = N.extract_all(rows, expected_with_utr=2)
    assert [e.utr for e in out] == ["UTR111111", None, "UTR222222"]


# ----------------------------------------------------------------- manifest
def test_manifest_declares_the_closed_world_calendar_and_the_honesty_clause(dev_dir):
    import json

    m = json.loads((dev_dir / emit.MANIFEST).read_text())
    assert m["calendar"]["declared_closed_world"] is True
    assert "asserts nothing about real bank closures" in m["calendar"]["note"]
    assert m["honesty_clause"].startswith("The data generator and the classifier share")
    assert m["counts"]["payments"] == plan.TARGET_ORDERS
    assert m["bank_rows_with_utr"] > 0
