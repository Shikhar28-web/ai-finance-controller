"""The answer key: fault -> true state. Authored before the classifier exists.

THE INDEPENDENCE CONSTRAINT
---------------------------
This mapping is the answer key; the SPEC 7 cascade is the student. If the key were
derived by tracing the cascade, the confusion matrix would measure only whether the
classifier agrees with itself, and every diagonal cell would be guaranteed. So this
file is written from two sources only: the fault descriptions in SPEC 4, and the
plain-language conditions in the SPEC 7 table. It is committed to git BEFORE
afc/core/decide.py is written, and the history is the evidence -- an assertion of
independence is worth less than a commit that predates the thing it is independent of.

Its honest limit: one author wrote both this file and the cascade, so the two are not
independent in the way two teams would be. The diff at step 5 catches genuine slips,
not shared blind spots. That limitation is disclosed rather than papered over, and it
is why the diff is reviewed by the owner before either side is changed.

THE COMPOSITION RULE FOR COMPOUND FAULTS
----------------------------------------
Severity ordering alone is not sufficient. Two faults that both perturb the
settlement-to-bank gap interact through attribution: whether the decomposer names
both, one, or over-attributes across baselines is a property of the decomposer, not
of the data. Their true state would therefore depend on runtime behaviour, which is
precisely what an answer key may not do.

RULE HISTORY. Severity ordering (UNRESOLVED > HUMAN_REVIEW > AWAITING_BANK >
EXPLAINED > VERIFIED) was the originally approved composition rule. It was superseded
by the axis rule below and is now retained only as a cross-check: it reproduces 7 of
the 8 admissible pairs, disagreeing on exactly one, which is recorded inline. The
argument for replacing it is that the axis rule, applied without reference to the
classifier, independently excluded fee_rate_drift + duplicate_bank_credit -- the pair
the owner had predicted would be attribution-dependent. Severity ordering would have
admitted it.

So admissibility is decided by AXIS, not by severity:

    a compound pair is admissible in the held-out set if and only if
    its two faults perturb DIFFERENT axes.

Axes are the independent ways a record can go wrong:

    gap       the settlement-to-bank difference arithmetic
    ledger    whether the merchant's books agree
    presence  whether the bank credit exists yet at all
    keying    whether the match is keyed or inferred

Same-axis pairs stay in the dev set, where they can be studied, and are excluded from
held-out with the reason recorded below.
"""

from __future__ import annotations

from dataclasses import dataclass

from afc.config import (
    AWAITING_BANK,
    EXPLAINED,
    HUMAN_REVIEW,
    MATERIALITY_PAISE,
    UNRESOLVED,
    VERIFIED,
)

GAP, LEDGER, PRESENCE, KEYING, NONE = "gap", "ledger", "presence", "keying", "none"

# Sentinel: true state is fixed at injection time from the magnitude injected, not at
# classification time. Still deterministic -- the generator knows what it injected.
BY_MAGNITUDE = "BY_MAGNITUDE"


@dataclass(frozen=True)
class FaultSpec:
    axis: str
    true_state: str
    reason: str
    compoundable: bool = True


CLEAN_STATE = VERIFIED

SINGLE_FAULTS: dict[str, FaultSpec] = {
    "fee_rate_drift": FaultSpec(
        axis=GAP,
        true_state=EXPLAINED,
        reason="Amounts differ, and recomputing fee and GST from the contracted rate "
               "attributes every rupee of the difference. SPEC 7 EXPLAINED.",
    ),
    "missing_utr": FaultSpec(
        axis=KEYING,
        true_state=HUMAN_REVIEW,
        reason="No UTR, so Level 3 falls back to amount-and-date. SPEC 7 HUMAN_REVIEW: "
               "'match was inferred rather than keyed'. Assumes the fallback finds "
               "exactly one candidate; zero or many is a different record.",
    ),
    "bank_credit_missing": FaultSpec(
        axis=PRESENCE,
        true_state=UNRESOLVED,
        reason="No bank row at all, generated past the T+2 window so it is not a "
               "timing case. SPEC 7 UNRESOLVED: 'overdue past T+2 with no bank credit'.",
    ),
    "bank_amount_mismatch": FaultSpec(
        axis=GAP,
        true_state=BY_MAGNITUDE,
        reason="A gap with no attributable cause. SPEC 7 splits on size: 'material gap, "
               "no supporting evidence' is UNRESOLVED; 'unexplained_amount > 0 but "
               "bounded' is HUMAN_REVIEW. Injected at both magnitudes so both are "
               "exercised, and the true state is fixed by the magnitude injected.",
    ),
    "refund_not_in_ledger": FaultSpec(
        axis=LEDGER,
        true_state=HUMAN_REVIEW,
        reason="The settlement-to-bank axis reconciles; the books are missing the "
               "refund. SPEC 7 VERIFIED requires 'ledger agrees', which fails, and the "
               "cause is known exactly, so it is not UNRESOLVED's 'no supporting "
               "evidence'.",
    ),
    "duplicate_ledger_entry": FaultSpec(
        axis=LEDGER,
        true_state=HUMAN_REVIEW,
        reason="Same as above: bank axis clean, books wrong, cause and amount known "
               "exactly. A bookkeeping correction is a human task, not an auto-reconcile.",
    ),
    "duplicate_bank_credit": FaultSpec(
        axis=GAP,
        true_state=HUMAN_REVIEW,
        reason="SPEC 7 CHANGE, MADE EXPLICITLY BY THE OWNER -- not a mapping choice. "
               "Read literally SPEC 7 gives EXPLAINED, because the duplicate accounts "
               "for the whole difference and unexplained_amount is 0. But EXPLAINED is "
               "auto-reconcilable and product rule 3 forbids silently reconciling "
               "uncertain money; a duplicated credit will very likely be clawed back. "
               "Attributing a gap is not the same as the gap being benign, and SPEC 7 "
               "conflated the two. EXPLAINED now additionally requires that no "
               "attributed cause is itself an integrity defect, and Level 0 duplicate "
               "findings route to HUMAN_REVIEW. Consequence, recorded in the README: "
               "EXPLAINED is sourced by fee_rate_drift alone on this dataset.",
    ),
    "orphan_bank_credit": FaultSpec(
        axis=NONE,
        true_state=UNRESOLVED,
        reason="SPEC 7 UNRESOLVED: 'orphan record with no counterpart'.",
        compoundable=False,  # no payment or settlement to carry a second fault
    ),
    "timing_lag": FaultSpec(
        axis=PRESENCE,
        true_state=AWAITING_BANK,
        reason="SPEC 7 AWAITING_BANK: 'settlement processed, no bank credit, inside "
               "the T+2 working-day window'.",
    ),
    "timing_overdue": FaultSpec(
        axis=PRESENCE,
        true_state=UNRESOLVED,
        reason="SPEC 7 UNRESOLVED: 'overdue past T+2 with no bank credit'.",
    ),
}


def state_for_bank_amount_mismatch(delta_paise: int) -> str:
    """Fixed at injection time from the magnitude injected. SPEC 7's material/bounded split."""
    return UNRESOLVED if abs(delta_paise) >= MATERIALITY_PAISE else HUMAN_REVIEW


@dataclass(frozen=True)
class CompoundSpec:
    true_state: str
    reason: str


# Cross-axis pairs. Each true state is decided by the axes involved, not by which
# component is nastier, and none depends on what the decomposer does at runtime.
COMPOUND_PAIRS: dict[tuple[str, str], CompoundSpec] = {
    ("fee_rate_drift", "duplicate_ledger_entry"): CompoundSpec(
        HUMAN_REVIEW,
        "Gap fully attributed to fee drift, but the books are wrong; ledger failure "
        "blocks EXPLAINED regardless of attribution.",
    ),
    ("fee_rate_drift", "refund_not_in_ledger"): CompoundSpec(
        HUMAN_REVIEW,
        "Same: the bank axis explains itself, the ledger axis does not.",
    ),
    ("fee_rate_drift", "missing_utr"): CompoundSpec(
        HUMAN_REVIEW,
        "Gap is fully attributable, but the match is inferred rather than keyed, and "
        "SPEC 7 sends inferred matches to HUMAN_REVIEW whatever the arithmetic says.",
    ),
    ("missing_utr", "duplicate_ledger_entry"): CompoundSpec(
        HUMAN_REVIEW,
        "Both components are HUMAN_REVIEW on their own axes; no interaction.",
    ),
    ("duplicate_bank_credit", "duplicate_ledger_entry"): CompoundSpec(
        HUMAN_REVIEW,
        "Both components are HUMAN_REVIEW on their own axes after the SPEC 7 change "
        "to duplicate_bank_credit; no interaction.",
    ),
    ("bank_amount_mismatch", "duplicate_ledger_entry"): CompoundSpec(
        UNRESOLVED,
        "Injected at material magnitude, so an unattributable material gap stands "
        "regardless of the ledger fault. UNRESOLVED outranks HUMAN_REVIEW.",
    ),
    ("timing_overdue", "duplicate_ledger_entry"): CompoundSpec(
        UNRESOLVED,
        "Overdue with no bank credit is UNRESOLVED on its own and the ledger fault "
        "cannot improve it.",
    ),
    ("timing_lag", "duplicate_ledger_entry"): CompoundSpec(
        AWAITING_BANK,
        "MAPPING JUDGEMENT, NOT A SPEC 7 DERIVATION. Both conditions hold and SPEC 7 "
        "states no precedence. THE ONE PLACE THE TWO RULES DISAGREE, kept flagged "
        "deliberately: severity ordering says HUMAN_REVIEW (the ledger fault is "
        "nastier); the axis reading says AWAITING_BANK, because the record is not yet "
        "terminal -- the credit has not arrived, so no terminal state is knowable, and "
        "the ledger fault resurfaces once it lands. The owner ruled for AWAITING_BANK. "
        "Both positions are recorded because the disagreement is itself evidence the "
        "key was not derived mechanically from the cascade.",
    ),
}

# Same-axis pairs. Excluded from held-out because their true state would depend on
# decomposer behaviour rather than on what was injected. Kept in dev to study.
DEV_ONLY_PAIRS: dict[tuple[str, str], str] = {
    ("fee_rate_drift", "duplicate_bank_credit"):
        "Both perturb the gap. Fee drift moves the expected side, the duplicate moves "
        "the actual side, and whether the decomposer attributes both cleanly or "
        "double-counts across baselines is a property of the decomposer. The owner "
        "predicted this pair; it is excluded for exactly that reason.",
    ("fee_rate_drift", "bank_amount_mismatch"):
        "Both perturb the gap. Whether the residual after fee attribution equals the "
        "injected mismatch depends on attribution order and baseline choice.",
    ("duplicate_bank_credit", "bank_amount_mismatch"):
        "Both perturb the gap. The duplicate inflates the actual credit and the "
        "mismatch perturbs it again, so the residual left after duplicate attribution "
        "is not the injected mismatch unless the decomposer subtracts the duplicate "
        "first. That ordering is a decomposer property, so the true state is not "
        "knowable from what was injected.",
}


def axis_of(fault: str) -> str:
    return SINGLE_FAULTS[fault].axis


def is_cross_axis(pair: tuple[str, str]) -> bool:
    a, b = pair
    return axis_of(a) != axis_of(b)
