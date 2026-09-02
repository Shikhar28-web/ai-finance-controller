# AI Finance Controller

**Every rupee gets a verified trail.**

Reconciliation across three sources — Razorpay, the merchant's bank statement, and
the merchant's ledger — with a named, arithmetic-proven cause for every difference
and an honest exception list for everything it cannot explain.

> Razorpay tells you where the money went. We prove that the books agree.

## Status

Scaffold, firewall and CI only. No accuracy number exists yet. When one does, it
appears here alongside the honesty clause below, and never on its own.

```bash
make check     # ruff -> firewall -> README sync -> pytest
```

## The one architectural rule

The LLM is not the financial source of truth.

```
question / exception
  -> deterministic DB queries
  -> deterministic financial arithmetic
  -> evidence object
  -> LLM writes prose about the evidence
  -> response
```

No LLM output is read by any classification, scoring, matching or arithmetic path.
LLM text lands in a display-only field tagged `ai_generated: true`. If every LLM
call returned nonsense, every reported metric would be identical.

This is enforced three ways, not asserted once:

1. **Import layering** — `tools/check_imports.py` walks the AST of every module
   under `afc/` and fails the build if `afc.core`, `afc.metrics`, `afc.generate` or
   `afc.ingest` can so much as import `afc.llm`, a vendor SDK, or a network module.
   The LLM's only output type is defined inside `afc.llm`, so guarded code cannot
   even name it.
2. **Clock and entropy ban** — the same packages may not call `datetime.now()`,
   `date.today()`, `time.time()`, `uuid4()`, or module-level `random.*`.
   `AWAITING_BANK` vs `UNRESOLVED` turns on a T+2 window, so a wall-clock read
   would silently reclassify a frozen dataset overnight and make the held-out
   confusion matrix unreproducible. Randomness comes from an injected
   `random.Random(seed)`.
3. **Behavioural proof** — the pipeline is run twice with two differently seeded
   garbage LLM stubs, and the `metrics` block of `metrics_summary.json` must be
   byte-identical. *(Built at step 6.)*

`metrics_summary.json` is split into `metrics` (diffed) and `run_meta` (wall-clock,
throughput, git sha, model — excluded from the diff). Throughput is a reported
number but it is not a claim about correctness, and a garbage stub runs faster than
a real call, so timing cannot be part of the identity check.

## Money

Integer paise everywhere; rates as integer basis points. No float touches currency.

`round(gross * mdr_rate)` as written in the spec is ambiguous, and both readings are
wrong in different places:

| gross | MDR | float product | `round()` | correct |
|---|---|---|---|---|
| 1500 paise | 1.90% | exactly `28.5` | **28** — banker's rounding ties to even | 29 |
| 1500 paise | 2.10% | `31.500000000000004` — not the true tie | 32 — accidentally right | 32 |

The defect is rate-dependent, so it survives casual testing. `afc/money.py` uses
integer basis points with halves rounded away from zero: platform-stable, no float,
no ties-to-even.

## Constants

Every tunable value, with its justification. Generated from `afc/config.py`; CI
fails if this table drifts out of sync.

<!-- BEGIN GENERATED CONSTANTS -->

| Constant | Value | Why this value |
|---|---|---|
| `AUTO_THRESHOLD` | `0.9` | 0.90. Gates the auto-reconcile action, never the state label. Tuned on the dev set only; the held-out cost of this choice is reported, not hidden. |
| `CHECK_WEIGHTS` | `all 1.0` | All nine checks weigh 1.0, so confidence is literally 'N of 9 checks passed'. There is no labelled data to fit unequal weights against, and an unjustified 3x multiplier is exactly the 'vibes' SPEC 8 rejects. |
| `CONTRACTED_MDR_BPS` | `190` (1.9%) | 1.90% in integer basis points. The contracted baseline the decomposer recomputes expected fees from (SPEC 6 step 1). Owned by the classifier, not a fourth data source. Integer bps so no float ever touches money. |
| `GST_ON_FEE_BPS` | `1,800` (18%) | 18.00% in integer basis points. GST is charged on the fee, not the transaction amount (SPEC 4). |
| `INFERRED_MATCH_WINDOW_WORKING_DAYS` | `3` | 3 working days. The search window for the UTR-missing fallback in SPEC 5 Level 3. One day wider than T+2 so a legitimately late credit is found and marked inferred rather than being missed entirely. |
| `MATERIALITY_PAISE` | `10,000` (Rs 100.00) | Rs 100.00. The HUMAN_REVIEW/UNRESOLVED boundary. Set at 100x tolerance: below it a residual is plausibly arithmetic noise worth a human's minute; above it something structural is wrong. Tuned on the dev set only. |
| `REVIEW_THRESHOLD` | `0.6` | 0.60. Gates 'AI suggestion, human confirms'. Below it, straight to the human queue. Tuned on the dev set only. |
| `ROUNDING_CAP_CENTIPAISE_PER_PAYMENT` | `109` | 1.09 paise per payment, in centipaise to stay integer. The hard bound on accumulated rounding: 0.5 (fee) + 0.5 (GST) + 0.18*0.5 (GST on a rounded fee). Anything beyond the cap is unexplained, not rounding. |
| `SEED_DEV` | `42` | 42. Fixed seed for the dev split, which we tune against freely. |
| `SEED_HELDOUT` | `1337` | 1337. Fixed seed for the held-out split. Never inspected, never tuned against, run once at the end. |
| `SETTLEMENT_T_PLUS_WORKING_DAYS` | `2` | T+2, per SPEC 4. Working days exclude Sundays, the 2nd and 4th Saturday, and bank holidays. Drives the AWAITING_BANK / UNRESOLVED boundary. |
| `TOLERANCE_PAISE` | `100` (Rs 1.00) | Rs 1.00. SPEC 7 suggests it. Absorbs accumulated paise rounding across a settlement without being large enough to hide a real fault. Note: this constant never binds on our synthetic data -- generator and classifier share arithmetic, so a clean settlement's gap is exactly 0 and every injected fault clears Rs 1 by more than an order of magnitude. It is exercised by unit-test fixtures and exists for real-file robustness. |

<!-- END GENERATED CONSTANTS -->

## Deviations from SPEC.md

Each of these was raised with the owner and approved before any code was written.

- **Level 2 deleted.** SPEC 5 defines Level 2 as checking that a settlement's
  payment count and gross total equal the sum of its constituent payments. Because
  the generator builds settlements by grouping its own payments, that check is
  tautologically true and no fault in the table can break it — it would have
  reported 100% while doing no work. We ship a **Level 0 integrity pass plus two
  matching levels** (payment axis, and settlement↔bank axis). This project does not
  claim a three-level matcher.
- **Level 0 added.** Duplicate and completeness detection, run before the matching
  levels. SPEC 5 declares Level 1 and Level 3 as 1:1 joins, but `duplicate_ledger_entry`
  produces 1:2 — a 1:1 join would silently dedup and never detect the fault it was
  graded on. Level 0 does group-by-count on ledger rows by `payment_id` and bank rows
  by UTR, plus a refund-completeness check, and `.first()` is banned in matcher joins.
- **Fault table cut from 14 to 10** (plus compound built from those): `fee_rate_drift`,
  `missing_utr`, `bank_credit_missing`, `bank_amount_mismatch`, `refund_not_in_ledger`,
  `duplicate_ledger_entry`, `duplicate_bank_credit`, `orphan_bank_credit`, `timing_lag`,
  `timing_overdue`. Fourteen fault types across 500 records gives confusion-matrix cells
  with two or three instances each; ten gives a defensible matrix. A sparse matrix
  honestly reported is still a worse number. Dropped: `rounding_drift` (circular — the
  decomposer has a rounding bucket, so injecting the fault would grade us on our own
  arithmetic), `ledger_amount_mismatch`, `adjustment_unexplained` (the sign-convention
  work SPEC 4 leaves undefined is not worth it at this deadline).
- **`adjustment_variance` is fixture-tested only.** With `adjustment_unexplained`
  dropped, no generated fault reaches that decomposer branch. The branch stays (SPEC 6
  specifies it, and a real file would need it) but the batch metrics never exercise it.
  Four of the five named causes are exercised by the dataset.
- **SPEC 7 parameterised, not extended.** The table's `bounded` and `Material` are the
  only numeric predicates in it with no numeric definition, so two of five states could
  not be coded as written. They are now the named constants `MATERIALITY_PAISE` and
  `TOLERANCE_PAISE`, and the rows are evaluated in a fixed precedence order, first match
  wins. No state was added and no condition invented.
- **One clause completed on HUMAN_REVIEW.** VERIFIED's condition already ends with
  "ledger agrees", so `ledger_agrees` is a first-class term in SPEC 7 — but the table
  never says what happens when it is false and everything else passes. A keyed,
  bank-reconciling settlement carrying `duplicate_ledger_entry` matched *zero* rows.
  HUMAN_REVIEW now also fires when `ledger_agrees` is false while the settlement-to-bank
  axis reconciles. Not EXPLAINED, because EXPLAINED is auto-reconcilable and
  auto-reconciling a record whose books are demonstrably wrong breaks product rule 3.
  Not UNRESOLVED, because we know exactly which row is duplicated and by how much.

  Evaluation order, first match wins:

  | # | State | Condition |
  |---|---|---|
  | R1 | UNRESOLVED | no counterpart on either side (orphan bank credit; payment-less settlement) |
  | R2 | AWAITING_BANK | settled, no bank credit, `as_of_date <= t_plus(settled_date, 2)` |
  | R3 | UNRESOLVED | settled, no bank credit, past T+2 |
  | R4 | HUMAN_REVIEW | match was inferred rather than keyed |
  | R5 | UNRESOLVED | `abs(unexplained) >= MATERIALITY_PAISE` |
  | R6 | HUMAN_REVIEW | `0 < abs(unexplained) < MATERIALITY_PAISE` |
  | R7 | HUMAN_REVIEW | `ledger_agrees` is false |
  | R8 | VERIFIED | keyed both levels, `abs(gap) <= TOLERANCE_PAISE`, no attributions, ledger agrees |
  | R9 | EXPLAINED | `unexplained == 0` and at least one attribution |
  | R10 | UNRESOLVED | fallback — asserts; should be unreachable |

  R8 and R9 are disjoint on `len(attributed)`, not on ordering alone, so the
  "40 paise inside a 100 paise tolerance" overlap is closed independently of the
  cascade. Implemented at step 5; recorded here now because it is approved.
- **Step 9 (Razorpay test-mode adapter) cut.** Below the protected line, and the
  deadline decided it.

## Not built, and why

- Invoice OCR / PDF / image ingestion, GST or ITC eligibility logic, tax scoring —
  out of scope per SPEC 2. Tax rules change, and getting them subtly wrong in front
  of finance judges is worse than not shipping them.

## Honesty

> The data generator and the classifier share the same fee arithmetic, so single-cause faults are correct by construction.
> This score demonstrates internal coherence, not that the system would survive a real settlement file.
> The decomposer's cause list and the generator's fault list are the same closed set, so unexplained_amount is near-zero by construction and “unknown is a valid answer” is demonstrated here rather than stress-tested.
> A real settlement file would contain causes neither list anticipates.
> The fault-to-state ground truth this matrix is scored against was authored by us, from the same model of correctness as the classifier.

All five sentences appear verbatim here and in `metrics_summary.json`. The UI shows
the first next to the number, with the rest behind an expand labelled *why this
number is qualified* — same disclosure, and nothing is behind that click which is
not also in two plaintext files. Rendered from `afc/config.py`; the split is
asserted to reconstitute the full text exactly.

No target metric is hardcoded anywhere. The engine reports what it actually
produces, including failures.
