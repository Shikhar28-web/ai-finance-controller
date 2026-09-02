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
   would silently reclassify a frozen dataset overnight and make the sealed_eval
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
| `AUTO_THRESHOLD` | `0.9` | 0.90. Gates the auto-reconcile action, never the state label. Tuned on the dev set only; what it costs on sealed_eval data is reported, not hidden. |
| `CHECK_WEIGHTS` | `all 1.0` | All nine checks weigh 1.0, so confidence is literally 'N of 9 checks passed'. There is no labelled data to fit unequal weights against, and an unjustified 3x multiplier is exactly the 'vibes' SPEC 8 rejects. |
| `CONTRACTED_MDR_BPS` | `190` (1.9%) | 1.90% in integer basis points. The contracted baseline the decomposer recomputes expected fees from (SPEC 6 step 1). Owned by the classifier, not a fourth data source. Integer bps so no float ever touches money. |
| `GST_ON_FEE_BPS` | `1,800` (18%) | 18.00% in integer basis points. GST is charged on the fee, not the transaction amount (SPEC 4). |
| `INFERRED_MATCH_WINDOW_WORKING_DAYS` | `3` | 3 working days. The search window for the UTR-missing fallback in SPEC 5 Level 3. One day wider than T+2 so a legitimately late credit is found and marked inferred rather than being missed entirely. |
| `MATERIALITY_PAISE` | `10,000` (Rs 100.00) | Rs 100.00. The HUMAN_REVIEW/UNRESOLVED boundary. Set at 100x tolerance: below it a residual is plausibly arithmetic noise worth a human's minute; above it something structural is wrong. Tuned on the dev set only. |
| `REVIEW_THRESHOLD` | `0.6` | 0.60. Gates 'AI suggestion, human confirms'. Below it, straight to the human queue. Tuned on the dev set only. |
| `ROUNDING_CAP_CENTIPAISE_PER_PAYMENT` | `109` | 1.09 paise per payment, in centipaise to stay integer. The hard bound on accumulated rounding: 0.5 (fee) + 0.5 (GST) + 0.18*0.5 (GST on a rounded fee). Anything beyond the cap is unexplained, not rounding. |
| `SEED_DEV` | `42` | 42. Fixed seed for the dev split, which we tune against freely. |
| `SEED_SEALED_EVAL` | `1337` | 1337. The seed the headline number is measured on. Never inspected, never tuned against, run once at the end. Tests generalisation to unseen amounts, dates and refund incidence under an identical fault distribution -- not to an unseen fault mix, which is why it is not called held-out. |
| `SEED_SEALED_EVAL_RESERVE` | `(2027, 3119)` | (2027, 3119). Two unopened reserve seeds. Re-running the seed already seen after a bug fix would be tuning against evaluation data; opening a reserve seed instead keeps the measurement clean, and the README logs every burn so a reader can count how many looks the number has had. |
| `SETTLEMENT_T_PLUS_WORKING_DAYS` | `2` | T+2, per SPEC 4. Working days exclude Sundays, the 2nd and 4th Saturday, and bank holidays. Drives the AWAITING_BANK / UNRESOLVED boundary. |
| `TOLERANCE_PAISE` | `100` (Rs 1.00) | Rs 1.00. SPEC 7 suggests it. Absorbs accumulated paise rounding across a settlement without being large enough to hide a real fault. Note: this constant never binds on our synthetic data -- generator and classifier share arithmetic, so a clean settlement's gap is exactly 0 and every injected fault clears Rs 1 by more than an order of magnitude. It is exercised by unit-test fixtures and exists for real-file robustness. |

<!-- END GENERATED CONSTANTS -->

## The dataset

500 orders over **1 Jun – 31 Jul 2026**, evaluated as of **31 Jul 2026** — a frozen
date, never a clock read, chosen so the final three working days sit inside their
T+2 window and host the `timing_lag` settlements.

The working-day calendar is a **declared closed world**: working days are Sundays,
2nd/4th Saturdays and an explicit fixed-date holiday list, and the dataset asserts
nothing about real Indian bank closures in that period. The dates are coordinates,
not history, so no settlement can land on a day the bank was "really" shut. Same
move as the contracted MDR: a declared input, not a claim about the world.

**Unit of classification: 518 recon units** — one per payment (500), one per orphan
bank credit (18). SPEC 7 says every transaction lands in one state, but AWAITING_BANK
is a property of a settlement and orphan-record a property of a bank row; the
confusion matrix needs one declared denominator. 482 units are scored; 36 are
excluded (below).

Dev and sealed_eval come from **`generate(seed)` and nothing else** — no structural
switch exists. Seeds: dev `42`, sealed_eval `1337`, sealed `2027` and `3119`. If a
genuine bug forces a second sealed_eval run, a sealed seed is burned rather than
re-running one already seen, and the burn is logged here.

**Reserve seeds burned: 1** — `2027`, opened to build the distribution-shift split
below. One reserve remains (`3119`).

### Distribution shift (secondary)

`sealed_eval` holds the fault distribution fixed, so it says nothing about robustness
to a different mix. A third split, generated by the same code path on the burned
reserve seed with reweighted class proportions, gives one cheap signal:

| State | sealed_eval | shifted | move |
|---|---|---|---|
| VERIFIED | 35.7% | 52.3% | +16.7pp |
| EXPLAINED | 5.8% | 3.1% | −2.7pp |
| AWAITING_BANK | 6.4% | 3.1% | −3.3pp |
| HUMAN_REVIEW | 32.2% | 13.7% | −18.5pp |
| UNRESOLVED | 19.9% | 27.7% | +7.8pp |

Results on it are reported as a **secondary number and expected to be worse**, and
**as an aggregate only** — overall accuracy and the state distribution. 13 of its
classes fall under the 15-unit floor, so per-state precision and recall on this split
are **deliberately not reported**: a noisy per-class table invites more weight than it
can carry. It does not gate the build.

See **Limits that travel with the number** under Honesty — the density, base-rate and composition caveats are reported with the accuracy figure, not here.

### Required in every metrics output

`metrics_summary.json` must carry these as visible lines, not README-only prose:

- the classification denominator and the scored population size
- units excluded from scoring and why (36 same-axis compound units)
- **bank rows that could not be keyed** (4) — rows with no extractable UTR are
  excluded from the Level 0 duplicate check and can only reach Level 3 through the
  inferred fallback. That is a real limit on coverage, not an implementation detail
- the full honesty clause, verbatim
- fault-carrying-only breakdown alongside the all-units breakdown

### Census

`make census` prints it and fails if any scored sealed_eval class falls under 15 units.

| | dev | sealed_eval |
|---|---|---|
| recon units | 518 | 518 |
| scored | 482 | 482 |
| excluded (same-axis pairs) | 36 | 36 |
| smallest scored fault class | 15 | 15 |
| clean share of scored units | 35.7% | 35.7% |

True-state distribution (scored units), identical across splits by construction:

| State | units | share | fault-only | share |
|---|---|---|---|---|
| VERIFIED | 172 | 35.7% | 0 | 0.0% |
| EXPLAINED | 28 | 5.8% | 28 | 9.0% |
| AWAITING_BANK | 31 | 6.4% | 31 | 10.0% |
| HUMAN_REVIEW | 155 | 32.2% | 155 | 50.0% |
| UNRESOLVED | 96 | 19.9% | 96 | 31.0% |

## Deviations from SPEC.md

Each of these was raised with the owner and approved before any code was written.

- **Level 2 deleted.** SPEC 5 defines Level 2 as checking that a settlement's
  payment count and gross total equal the sum of its constituent payments. Because
  the generator builds settlements by grouping its own payments, that check is
  tautologically true and no fault in the table can break it — it would have
  reported 100% while doing no work. We ship a **Level 0 integrity pass plus two
  matching levels** (payment axis, and settlement↔bank axis). This project does not
  claim a three-level matcher.
- **Level 3 tie-break policy.** With a UTR present the match is *keyed*. With the UTR
  missing, the fallback searches bank rows carrying no extractable UTR for one within
  both the amount tolerance and the date window. **Ambiguous means more than one row
  satisfies both conditions**, and an ambiguous settlement is an **unmatched record**
  routed by R1 or R3 — never a pick. No nearest-neighbour, no first-in-file, no
  lowest-id: any of those makes the result depend on row order, and an irreproducible
  `sealed_eval` is worse than an unmatched record. `UNMATCHED_AMBIGUOUS` and
  `UNMATCHED_NO_CANDIDATE` are recorded separately so the evidence distinguishes
  "two possible" from "none found".

  On this dataset the ambiguous branch **never fires** — all 4 `missing_utr`
  settlements resolve to exactly one candidate. Like `TOLERANCE_PAISE`, it is
  fixture-tested rather than exercised by batch data, and reported that way.
- **Level 1 consumes Level 0 rather than re-deriving.** Duplicate status arrives as a
  set of flagged payment ids; if Level 1 recomputed it the two could drift and the
  cascade would see inconsistent evidence for one record. A test mutates a Level 0
  finding both ways and asserts Level 1's output follows.
- **Level 0 added.** Duplicate detection, run before the matching levels. SPEC 5
  declares Level 1 and Level 3 as 1:1 joins, but `duplicate_ledger_entry` produces 1:2
  — a 1:1 join would silently dedup and never detect the fault it was graded on.
  `.first()`, `.one_or_none()` and `.find_one()` are banned in the guarded packages,
  enforced by `tools/check_imports.py`.

  **Counting is pinned**, because three different numbers get called "duplicates" and
  only one of them is a duplicate count: *groups* (keys appearing more than once),
  *excess rows* (rows in those groups minus one each), and *group members* (every row
  in those groups). At a duplication factor of exactly 2 the first two coincide — 106
  and 106 — while members is 212. Level 0 emits **one finding per group**; flagging
  members would double-count and pull clean records into HUMAN_REVIEW through R7,
  an error that reads like a classifier result rather than a counting bug. Expected
  counts in tests are derived from the injection plan *and* from ground truth
  independently, never written as literals.

  **Bank rows group by extracted UTR, never by narration.** Every `missing_utr`
  settlement writes the same narration text, so grouping on narration merges unrelated
  settlements into a phantom duplicate group — 6 groups and 8 excess rows instead of
  the true 5 and 5. Rows with no extractable UTR cannot be keyed at all; they are
  excluded from the check and reported.

  **Findings carry their axis.** `duplicate_ledger_entry` and `duplicate_bank_credit`
  both reach HUMAN_REVIEW but by different clauses — the R7 ledger clause and the R9
  integrity amendment. An undifferentiated flag would leave the cascade unable to pick
  the right rule, and the step-5 diff would report a plumbing gap as a mapping
  disagreement.
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
  | R7 | HUMAN_REVIEW | `ledger_agrees` is false, **or Level 0 found an integrity defect** (duplicate bank credit, duplicate ledger entry) |
  | R8 | VERIFIED | keyed both levels, `abs(gap) <= TOLERANCE_PAISE`, no attributions, ledger agrees |
  | R9 | EXPLAINED | `unexplained == 0`, at least one attribution, **and no attributed cause is itself an integrity defect** |
  | R10 | UNRESOLVED | fallback — asserts; should be unreachable |

  R8 and R9 are disjoint on `len(attributed)`, not on ordering alone, so the
  "40 paise inside a 100 paise tolerance" overlap is closed independently of the
  cascade. Implemented at step 5; recorded here now because it is approved.
- **A second SPEC 7 change, made explicitly.** Read literally, a duplicated bank
  credit is EXPLAINED: the duplicate accounts for the entire gap, so
  `unexplained_amount` is 0. But EXPLAINED is auto-reconcilable, and product rule 3
  forbids silently reconciling uncertain money — a duplicated credit is money that
  will very likely be clawed back. Attributing a gap is not the same as the gap
  being benign, and SPEC 7 conflated the two. R9 now requires that no attributed
  cause is itself an integrity defect, and R7 routes Level 0 duplicate findings to
  HUMAN_REVIEW.

  **Consequence, stated rather than left to be found: EXPLAINED is now sourced by
  `fee_rate_drift` alone on this dataset.** One fault class feeds one diagonal cell,
  so EXPLAINED precision and recall rest on a single generator behaviour. Read that
  cell with more suspicion than the others.
- **Step 9 (Razorpay test-mode adapter) cut.** Below the protected line, and the
  deadline decided it. No test-mode credentials are held.
- **Same-axis compound pairs are excluded at scoring, not at generation.** Keeping
  them for study and requiring dev and sealed_eval to differ by seed alone are only
  consistent if generation is identical, so all three same-axis pairs are generated
  in both splits and dropped from sealed_eval scoring with the count reported (36 units).

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

### Limits that travel with the number

These are not methodology footnotes. Any accuracy figure from this system is
reported with them.

- **The population is deliberately unlike production.** ~64% of scored units carry a
  fault; a real merchant's file is closer to ~5%. The density is what makes every
  fault class measurable at the 15-unit floor, and it makes this number
  non-comparable to accuracy on a real file. Per-state scores are therefore also
  reported over fault-carrying units only.
- **HUMAN_REVIEW is structurally flattered.** It holds 50% of fault-carrying units,
  so its precision and recall are inflated by base rate rather than by the classifier
  working well.
- **`sealed_eval` is not a held-out set in the usual sense.** It tests generalisation
  to unseen amounts, dates and refund incidence under an *identical* fault
  distribution — not to an unseen fault mix. It is named for exactly what it does.
- **EXPLAINED rests on one fault class.** `fee_rate_drift` is its only source, 28
  units, so that diagonal cell rests on a single generator behaviour.
- **The answer key and the classifier share an author.** The fault-to-state ground
  truth was written from the SPEC 4 fault definitions and the SPEC 7 table alone and
  committed to git before the classifier existed — the history, not an assertion, is
  the evidence. But one author wrote both, so the agreement diff catches genuine
  slips, not shared blind spots.

No target metric is hardcoded anywhere. The engine reports what it actually
produces, including failures.
