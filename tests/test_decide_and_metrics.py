"""Cascade, confidence, metrics harness, and the SPEC 3 firewall test."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from run_metrics import build  # noqa: E402

from afc.config import (  # noqa: E402
    AWAITING_BANK,
    EXPLAINED,
    HUMAN_REVIEW,
    MATERIALITY_PAISE,
    STATES,
    UNRESOLVED,
    VERIFIED,
)
from afc.core import confidence as conf  # noqa: E402
from afc.core import decide as D  # noqa: E402
from afc.core.decompose import Attribution, Decomposition  # noqa: E402
from afc.core.matching import INFERRED, KEYED, Level1Result, Level3Result  # noqa: E402
from afc.core.models import ORPHAN_BANK_CREDIT, PAYMENT  # noqa: E402
from afc.llm.narrate import GarbageNarrator  # noqa: E402

AS_OF = date(2026, 7, 31)


def ev(**kw):
    base = dict(
        unit_id="u1", unit_kind=PAYMENT,
        level1=Level1Result("p1", "o1", True, True, False, 100, 100),
        level3=Level3Result("s1", KEYED, "UTR1", ("b1",), 100, 100, False, 1),
        decomposition=Decomposition("s1", 100, 100, 0, (), 0),
        due_on=date(2026, 7, 20), as_of=AS_OF, integrity_defect=False,
    )
    base.update(kw)
    return D.Evidence(**base)


# ----------------------------------------------------------------- the table
def test_the_table_is_the_spec7_five_states_in_a_stated_order():
    assert [r[0] for r in D.TABLE] == [f"R{i}" for i in range(1, 11)]
    assert {r[1] for r in D.TABLE} == set(STATES)
    assert D.TABLE[-1][2](ev()) is True, "R10 must be unconditional"


def test_a_clean_keyed_record_is_verified_by_r8():
    d = D.decide(ev())
    assert (d.state, d.rule) == (VERIFIED, "R8")


def test_precedence_awaiting_bank_beats_the_ledger_clause():
    # Both R2 and R7 hold. R2 wins: the record is not terminal, so no terminal state
    # is knowable while the credit is outstanding.
    d = D.decide(ev(
        level3=Level3Result("s1", "unmatched_no_bank_row", "UTR1", (), 100, None, False, 0),
        level1=Level1Result("p1", "o1", True, True, True, 100, None),
        decomposition=None, due_on=date(2026, 8, 5)))
    assert (d.state, d.rule) == (AWAITING_BANK, "R2")


def test_past_the_window_the_same_record_is_unresolved_by_r3():
    d = D.decide(ev(
        level3=Level3Result("s1", "unmatched_no_bank_row", "UTR1", (), 100, None, False, 0),
        decomposition=None, due_on=date(2026, 7, 1)))
    assert (d.state, d.rule) == (UNRESOLVED, "R3")


def test_an_inferred_match_goes_to_human_review_by_r4():
    d = D.decide(ev(level3=Level3Result("s1", INFERRED, None, ("b1",), 100, 100, False, 1)))
    assert (d.state, d.rule) == (HUMAN_REVIEW, "R4")


def test_a_material_unexplained_gap_is_unresolved_and_a_bounded_one_is_review():
    big = Decomposition("s1", 100, 0, -MATERIALITY_PAISE, (), -MATERIALITY_PAISE)
    small = Decomposition("s1", 100, 60, -40, (), -40)
    assert D.decide(ev(decomposition=big)).rule == "R5"
    assert D.decide(ev(decomposition=big)).state == UNRESOLVED
    assert D.decide(ev(decomposition=small)).rule == "R6"
    assert D.decide(ev(decomposition=small)).state == HUMAN_REVIEW


def test_the_ledger_clause_r7_catches_a_bank_clean_record_with_bad_books():
    # The SPEC 7 hole: without R7's ledger clause this record matches no row at all.
    d = D.decide(ev(level1=Level1Result("p1", "o1", True, True, True, 100, None)))
    assert (d.state, d.rule) == (HUMAN_REVIEW, "R7")


def test_an_attributed_gap_is_explained_by_r9():
    dec = Decomposition("s1", 100, 60, -40, (Attribution("fee_rate_drift", -40, "x"),), 0)
    d = D.decide(ev(decomposition=dec))
    assert (d.state, d.rule) == (EXPLAINED, "R9")


def test_a_duplicate_credit_is_not_explained_despite_a_fully_attributed_gap():
    # The owner's SPEC 7 change: attributing a gap is not the same as the gap being
    # benign. R7 catches it on the integrity flag before R9 can call it EXPLAINED.
    dec = Decomposition("s1", 100, 200, 100,
                        (Attribution("duplicate_bank_credit", 100, "x"),), 0)
    d = D.decide(ev(decomposition=dec, integrity_defect=True))
    assert d.state == HUMAN_REVIEW
    assert dec.has_integrity_defect


def test_a_failed_decomposition_routes_to_unresolved_outside_the_table():
    dec = Decomposition("s1", 0, 0, 0, (), 0, failed=True, failure_reason="forced")
    d = D.decide(ev(decomposition=dec))
    assert d.state == UNRESOLVED
    assert d.rule == "DECOMPOSITION_FAILED"


# ----------------------------------------------------------------- confidence
def test_inapplicable_checks_are_excluded_not_counted_as_passes():
    d = D.decide(ev(unit_kind=ORPHAN_BANK_CREDIT, level1=None, level3=None,
                    decomposition=None, due_on=None))
    assert d.state == UNRESOLVED and d.rule == "R1"
    assert d.confidence.applicable == 0
    assert d.confidence.score == 0.0, "an orphan is the record we know least about"


def test_confidence_is_a_pass_rate_over_applicable_checks():
    c = conf.score({"payment_found": True, "order_id_matches": False,
                    "settlement_found": None})
    assert (c.applicable, c.passed, c.score) == (2, 1, 0.5)


def test_a_non_automatable_state_never_auto_reconciles():
    perfect = conf.score(dict.fromkeys(("payment_found", "ledger_agrees"), True))
    assert perfect.score == 1.0
    assert conf.route(UNRESOLVED, perfect) != conf.AUTO_RECONCILE
    assert conf.route(HUMAN_REVIEW, perfect) != conf.AUTO_RECONCILE
    assert conf.route(VERIFIED, perfect) == conf.AUTO_RECONCILE


# ----------------------------------------------------------------- metrics harness
@pytest.fixture(scope="module")
def dev_summary():
    return build("dev")


def test_every_scored_unit_lands_in_exactly_one_state(dev_summary):
    c = dev_summary["metrics"]["confusion"]
    assert sum(sum(row) for row in c["matrix_true_by_predicted"]) == c["scored"]


def test_the_required_metrics_lines_are_present(dev_summary):
    m = dev_summary["metrics"]
    for key in ("denominator", "population_scored", "units_excluded_same_axis_compounds",
                "unkeyed_bank_rows", "decomposition_failures", "honesty_clause",
                "confusion_fault_carrying_only", "attribution", "rupees"):
        assert key in m, key
    assert m["honesty_clause"].startswith("The data generator and the classifier share")
    assert m["units_excluded_same_axis_compounds"] == 36
    assert m["unkeyed_bank_rows"] == 4


def test_the_fault_only_matrix_excludes_clean_records(dev_summary):
    f = dev_summary["metrics"]["confusion_fault_carrying_only"]
    # Alignment bug guard: zipping against a separately-sorted list silently pairs
    # true states with the wrong fault classes, and VERIFIED support goes non-zero.
    assert f["per_state"]["VERIFIED"]["support"] == 0
    assert f["scored"] < dev_summary["metrics"]["confusion"]["scored"]


def test_timing_is_in_run_meta_not_in_metrics(dev_summary):
    assert "wall_clock_seconds" in dev_summary["run_meta"]
    assert "wall_clock_seconds" not in json.dumps(dev_summary["metrics"])


# ----------------------------------------------------------------- SPEC 3 firewall
def test_garbage_llm_output_leaves_every_metric_byte_identical():
    a, b = build("dev", GarbageNarrator(1)), build("dev", GarbageNarrator(2))
    assert json.dumps(a["metrics"], sort_keys=True) == json.dumps(b["metrics"], sort_keys=True)


def test_the_firewall_test_is_not_vacuous():
    # A firewall test that passes because no LLM call exists anywhere proves nothing.
    a, b = build("dev", GarbageNarrator(1)), build("dev", GarbageNarrator(2))
    assert a["ai_note"]["text"] != b["ai_note"]["text"]
    assert a["ai_note"]["ai_generated"] is True
