"""Every tunable constant in the system, in one place.

SPEC 7 and 8 require named constants rather than magic numbers, and the owner
requires that each one appear in the README with its value and a one-line
justification. JUSTIFICATIONS below is that single source: the README table is
rendered from it (tools/render_constants.py) and tests/test_config.py fails if
any public constant is missing an entry. A judge asking "why Rs 100?" gets an
answer that cannot drift out of date.
"""

from typing import Final

# ---------------------------------------------------------------- states (SPEC 7)
VERIFIED: Final = "VERIFIED"
EXPLAINED: Final = "EXPLAINED"
AWAITING_BANK: Final = "AWAITING_BANK"
HUMAN_REVIEW: Final = "HUMAN_REVIEW"
UNRESOLVED: Final = "UNRESOLVED"

STATES: Final = (VERIFIED, EXPLAINED, AWAITING_BANK, HUMAN_REVIEW, UNRESOLVED)

# Ground-truth severity ordering for compound faults: the true state of a record
# carrying several faults is the most severe of its components.
STATE_SEVERITY: Final = {
    UNRESOLVED: 4,
    HUMAN_REVIEW: 3,
    AWAITING_BANK: 2,
    EXPLAINED: 1,
    VERIFIED: 0,
}

# ---------------------------------------------------------------- money (paise)
TOLERANCE_PAISE: Final = 100
MATERIALITY_PAISE: Final = 10_000

# ---------------------------------------------------------------- contracted rates
CONTRACTED_MDR_BPS: Final = 190
GST_ON_FEE_BPS: Final = 1_800

# ---------------------------------------------------------------- calendar
SETTLEMENT_T_PLUS_WORKING_DAYS: Final = 2
INFERRED_MATCH_WINDOW_WORKING_DAYS: Final = 3

# ---------------------------------------------------------------- decomposer
ROUNDING_CAP_CENTIPAISE_PER_PAYMENT: Final = 109

# ---------------------------------------------------------------- confidence (SPEC 8)
CHECK_NAMES: Final = (
    "payment_found",
    "order_id_matches",
    "settlement_found",
    "utr_present",
    "utr_matches_bank",
    "amount_within_tol",
    "ledger_agrees",
    "no_duplicate_found",
    "gap_fully_attributed",
)
CHECK_WEIGHTS: Final = dict.fromkeys(CHECK_NAMES, 1.0)

AUTO_THRESHOLD: Final = 0.90
REVIEW_THRESHOLD: Final = 0.60

# ---------------------------------------------------------------- reproducibility
SEED_DEV: Final = 42

# SEALED_EVAL is the split the headline number is measured on. It is not a held-out set
# in the usual sense: it tests generalisation to unseen amounts, dates and refund
# incidence under an IDENTICAL fault distribution, not to an unseen fault mix. The name
# says exactly that much and no more.
#
# Three seeds generated, one measured, two kept in reserve. If a genuine bug forces a
# second evaluation run, a reserve seed is opened rather than re-reading the one already
# seen -- and the burn is logged in the README so a reader knows how many looks the
# number has had.
SEED_SEALED_EVAL: Final = 1_337
SEED_SEALED_EVAL_RESERVE: Final = (2_027, 3_119)

# ---------------------------------------------------------------- honesty (SPEC 9)
# SPEC9_SENTENCES is verbatim from SPEC 9 and must never be edited.
# ADDENDUM_SENTENCES were approved by the owner as additional disclosure: the first
# two because the decomposer's cause list and the generator's fault list are the same
# closed set, the third because we authored the answer key we are scored against.
SPEC9_SENTENCES: Final = (
    "The data generator and the classifier share the same fee arithmetic, so "
    "single-cause faults are correct by construction.",
    "This score demonstrates internal coherence, not that the system would "
    "survive a real settlement file.",
)
ADDENDUM_SENTENCES: Final = (
    "The decomposer's cause list and the generator's fault list are the same "
    "closed set, so unexplained_amount is near-zero by construction and "
    "\u201cunknown is a valid answer\u201d is demonstrated here rather than "
    "stress-tested.",
    "A real settlement file would contain causes neither list anticipates.",
    "The fault-to-state ground truth this matrix is scored against was authored "
    "by us, from the same model of correctness as the classifier.",
)

HONESTY_SENTENCES: Final = SPEC9_SENTENCES + ADDENDUM_SENTENCES

# Full text. Goes verbatim into the README and into metrics_summary.json.
HONESTY_CLAUSE: Final = " ".join(HONESTY_SENTENCES)

# UI placement: headline sits next to the number, detail goes behind an expand
# labelled "why this number is qualified". Nothing is behind the click that is not
# also in two plaintext files. Asserted in tests/test_config.py.
HONESTY_CLAUSE_HEADLINE: Final = HONESTY_SENTENCES[0]
HONESTY_CLAUSE_DETAIL: Final = " ".join(HONESTY_SENTENCES[1:])

JUSTIFICATIONS: Final = {
    "TOLERANCE_PAISE": (
        "Rs 1.00. SPEC 7 suggests it. Absorbs accumulated paise rounding across a "
        "settlement without being large enough to hide a real fault. Note: this "
        "constant never binds on our synthetic data -- generator and classifier "
        "share arithmetic, so a clean settlement's gap is exactly 0 and every "
        "injected fault clears Rs 1 by more than an order of magnitude. It is "
        "exercised by unit-test fixtures and exists for real-file robustness."
    ),
    "MATERIALITY_PAISE": (
        "Rs 100.00. The HUMAN_REVIEW/UNRESOLVED boundary. Set at 100x tolerance: "
        "below it a residual is plausibly arithmetic noise worth a human's minute; "
        "above it something structural is wrong. Tuned on the dev set only."
    ),
    "CONTRACTED_MDR_BPS": (
        "1.90% in integer basis points. The contracted baseline the decomposer "
        "recomputes expected fees from (SPEC 6 step 1). Owned by the classifier, "
        "not a fourth data source. Integer bps so no float ever touches money."
    ),
    "GST_ON_FEE_BPS": (
        "18.00% in integer basis points. GST is charged on the fee, not the "
        "transaction amount (SPEC 4)."
    ),
    "SETTLEMENT_T_PLUS_WORKING_DAYS": (
        "T+2, per SPEC 4. Working days exclude Sundays, the 2nd and 4th Saturday, "
        "and bank holidays. Drives the AWAITING_BANK / UNRESOLVED boundary."
    ),
    "INFERRED_MATCH_WINDOW_WORKING_DAYS": (
        "3 working days. The search window for the UTR-missing fallback in SPEC 5 "
        "Level 3. One day wider than T+2 so a legitimately late credit is found "
        "and marked inferred rather than being missed entirely."
    ),
    "ROUNDING_CAP_CENTIPAISE_PER_PAYMENT": (
        "1.09 paise per payment, in centipaise to stay integer. The hard bound on "
        "accumulated rounding: 0.5 (fee) + 0.5 (GST) + 0.18*0.5 (GST on a rounded "
        "fee). Anything beyond the cap is unexplained, not rounding."
    ),
    "CHECK_WEIGHTS": (
        "All nine checks weigh 1.0, so confidence is literally 'N of 9 checks "
        "passed'. There is no labelled data to fit unequal weights against, and an "
        "unjustified 3x multiplier is exactly the 'vibes' SPEC 8 rejects."
    ),
    "AUTO_THRESHOLD": (
        "0.90. Gates the auto-reconcile action, never the state label. Tuned on the "
        "dev set only; what it costs on sealed_eval data is reported, not hidden."
    ),
    "REVIEW_THRESHOLD": (
        "0.60. Gates 'AI suggestion, human confirms'. Below it, straight to the "
        "human queue. Tuned on the dev set only."
    ),
    "SEED_DEV": "42. Fixed seed for the dev split, which we tune against freely.",
    "SEED_SEALED_EVAL": (
        "1337. The seed the headline number is measured on. Never inspected, never "
        "tuned against, run once at the end. Tests generalisation to unseen amounts, "
        "dates and refund incidence under an identical fault distribution -- not to "
        "an unseen fault mix, which is why it is not called held-out."
    ),
    "SEED_SEALED_EVAL_RESERVE": (
        "(2027, 3119). Two unopened reserve seeds. Re-running the seed already seen "
        "after a bug fix would be tuning against evaluation data; opening a reserve "
        "seed instead keeps the measurement clean, and the README logs every burn so "
        "a reader can count how many looks the number has had."
    ),
}
