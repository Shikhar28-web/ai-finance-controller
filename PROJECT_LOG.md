# Project Log — AI Finance Controller

Everything built, every decision taken, and why. Written as the complete record of the
project for someone picking it up cold.

**Razorpay Buildathon, Track 04.** Reconciliation across three sources — Razorpay, the
merchant's bank statement, and the merchant's ledger — that names an arithmetic-proven
cause for every difference and an honest exception list for everything it cannot
explain.

---

## 1. At a glance

| | |
|---|---|
| Spec steps completed | **6 of 10** — steps 1–6, the protected "minimum shippable demo" |
| Commits | 18 |
| Python | 5,038 lines across 27 modules |
| Tests | **257**, all passing |
| Documentation | README 473 lines, FINDINGS 468, SPEC 301 |
| Defects caught and logged | 23 |
| Runtime dependencies | **zero** — Python 3.12 standard library only |
| Demo path | `make metrics`, verified on a clean clone |

### Headline numbers (`sealed_eval`, seed 1337, run once)

| Metric | Value |
|---|---|
| Accuracy | **1.0000** (482/482 recon units), all five states |
| `exact_cause_set_match_rate` | 1.0000 (36/36 settlements) |
| `rupee_attribution_error` | 0 paise |
| Coverage (terminal, no human) | **41.5%** |
| Cost of false positives | ₹0.00 |
| Rupees to human review | ₹424,388.25 |
| Rupees unresolved | ₹111,000.35 |
| Throughput | ~24,000 records/sec (518 units in 0.02s) |

**These numbers are internal coherence, not capability.** Section 12 explains why in
full. That framing is not modesty — it is the single most important thing to understand
about this project.

---

## 2. What is built and what is not

Against the §10 priority list:

| # | Deliverable | Status | Notes |
|---|---|---|---|
| 1 | Synthetic generator with labeled faults | **done** | 500 orders → 518 recon units, 3 splits, 10 fault classes + compounds |
| 2 | Normalization to common schema | **done** | 5 CSVs + manifest, exact round-trip, UTR extraction with coverage assertion |
| 3 | Three-level matcher | **done as Level 0 + 2 levels** | Level 2 deleted as tautological; the project does not claim three |
| 4 | Difference decomposer | **done** | named causes, signed rupee attribution, closure invariant |
| 5 | State machine + confidence + queue | **done** | R1–R10 as data, 9-check confidence, routing |
| 6 | Metrics harness | **done** | one command, 5×5 matrix, attribution metrics, JSON output |
| 7 | Dashboard + exception list | **partial** | static viewer at `docs/index.html`; no filterable exception table |
| 8 | Truth graph for one transaction | **not built** | |
| 9 | Razorpay test-mode adapter | **cut** | no test-mode credentials; below the protected line |
| 10 | Finance Copilot | **not built** | only a stub narrator, to make the §3 firewall test non-vacuous |

§10 states: *"Minimum shippable demo (steps 1-6 plus a bare UI). Protect steps 1-6 at
all costs."* Steps 1–6 are complete and tested, and `docs/index.html` is the bare UI.

---

## 3. The three non-negotiables, and how each is enforced

### §3 — the LLM firewall

*No LLM output may be read by any classification, scoring, matching or arithmetic code
path. If every LLM call returned garbage, every reported metric must be identical.*

Enforced three ways rather than asserted once:

1. **Import layering (structural).** `tools/check_imports.py` walks the AST of every
   module under `afc/` and fails the build if `afc.core`, `afc.metrics`, `afc.generate`
   or `afc.ingest` can so much as *import* `afc.llm`, a vendor SDK, or a network or
   storage module. `AiNote` — the only type LLM text may inhabit — is defined inside
   `afc.llm`, so guarded code cannot even name it.
2. **Clock and entropy ban.** The same packages may not call `datetime.now()`,
   `date.today()`, `time.time()`, `uuid4()`, `os.urandom()`, or module-level `random.*`.
   Randomness must come from an injected `random.Random(seed)`.
3. **Behavioural test.** The pipeline runs twice with two differently-seeded garbage
   narrators; the `metrics` block must be byte-identical **and** the `ai_note` must
   differ. The second assertion is what makes the first mean anything.

The `metrics` / `run_meta` split exists because §9 lists wall-clock throughput as a
metric, and a garbage stub is faster than a real call — so a naive byte-diff of the
whole file would fail against a perfectly firewalled system. Timing, git sha and
records/sec live in `run_meta` and are excluded from the diff.

### §7 — the decision table, implemented literally

`afc/core/decide.py` holds the table **once, as data**: an ordered tuple of
`(rule_id, state, predicate)`. First match wins. R10 is unconditional so the table
cannot fall through. It can be read against SPEC §7 line by line.

### §9 — the honesty clause

Five sentences, defined once in `afc/config.py` as the single source, emitted verbatim
into `metrics_summary.json` and the README, with the headline sentence beside the number
and the rest behind an expand in the viewer. A test asserts
`headline + " " + detail == full clause`, so nothing hides behind the click. No target
metric is hardcoded anywhere.

---

## 4. Build log, in order

### Hour 0–1 — scaffold, firewall, CI (`a0582a3`, `833b69d`)

Package skeleton with four guarded packages (`core`, `metrics`, `generate`, `ingest`)
plus `llm`. The firewall linter landed **before any guarded code existed**, deliberately,
and was verified against a deliberate violation: a module under `afc/core/` importing
the narrator and branching on its prose, importing `anthropic` and `sqlite3`, calling
`datetime.now()` in a state decision, and using the global RNG. All 7 caught, exit 1.
Pinned by 17 tests that write violating modules into the *real* guarded packages and run
the *real* linter.

### Integer-paise arithmetic and justified constants (`42a1521`)

`round(gross * mdr_rate)` as SPEC §4 writes it is wrong two different ways:

```
1500 paise @ 1.90%  ->  1500*0.019 is EXACTLY 28.5 in binary64
                        Python round() is banker's, ties to even -> 28   WRONG
                        finance rounds halves away from zero      -> 29
1500 paise @ 2.10%  ->  1500*0.021 is 31.500000000000004, not the tie
                        round() is then accidentally correct      -> 32
```

Rate-dependent, so it survives casual testing. `afc/money.py` uses **integer basis
points with halves away from zero** — no float, platform-stable — and rejects floats
with `TypeError` at the boundary. `parse_paise` refuses more than two decimal places
rather than guessing.

Every tunable constant carries a one-line justification in `afc/config.py`, and the
README table is **generated** from it; CI fails if it drifts.

### Hour 1–2 — the working-day calendar (`d9b6899`)

Working day = not a Sunday, not a declared holiday, not the 2nd or 4th Saturday.
**1st, 3rd and 5th Saturdays are working days** — the Indian banking convention.
Verified against two naive implementations that fail in opposite directions:

| Naive approach | Failure |
|---|---|
| T+2 *calendar* days | Settled Fri 09 Jan, our due Tue 13 Jan; naive says OVERDUE from Mon 12 Jan — after zero working days. **False positive on UNRESOLVED.** |
| every Saturday off | Settled Thu 01 Jan, our due Sat 03 Jan; naive says Mon 05 Jan. Two days late on 1st/3rd/5th Saturdays, so a genuinely overdue settlement still reads AWAITING_BANK. **False negative — money not chased.** |

Holidays are a **declared closed world**, carrying only fixed-date national holidays
(Republic Day, Independence Day, Gandhi Jayanti). Lunar-dated holidays — Diwali, Holi,
Eid — are excluded on purpose: shipping a subtly wrong Diwali date in front of finance
judges is the same class of error §2 puts out of scope. The manifest states the dataset
asserts nothing about real bank closures.

### The answer key, before the classifier (`5c65036`)

`afc/core/faults.py` maps each fault to its true state, authored from the §4 fault
descriptions and the §7 table, and **committed before `decide.py` existed**. See §12 for
why that ordering proves less than it appears to.

**Composition rule for compound faults — axis, not severity:**

> A compound pair belongs in the evaluation set **iff its two faults perturb different
> axes** (gap / ledger / presence / keying).

Two faults on the same axis interact through attribution, so their true state would
depend on decomposer behaviour — which an answer key may not do. Applied blind, this
rule independently excluded `fee_rate_drift + duplicate_bank_credit`, the pair the owner
had predicted would be attribution-dependent. Severity ordering, the previously approved
rule, would have admitted it; it is retained only as a cross-check and reproduces 7 of 8
pairs.

### Step 1 — the generator (`ea5867f`, `4f5c9fc`)

500 orders over **1 Jun – 31 Jul 2026**, evaluated at a frozen **`AS_OF = 2026-07-31`**.
`generate(seed)` is the only entry point and carries **no structural switch** — dev and
the evaluation split differ by seed alone.

### Step 2 — normalization (`de03522`)

Five CSVs plus a manifest; money written as rupee strings (the format that invites float
contamination) and parsed back through `parse_paise`. **Round trip is exact on every
seed.**

UTR extraction fails loudly: coverage is asserted against the manifest's known count.
Verified against the realistic mistake — assuming the reference is digits after the
prefix, true of settlement UTRs and false of orphan-credit references:

```
UTR extraction covered 37/55 rows that carry a UTR (67.3%).
A shortfall is indistinguishable from injected missing_utr faults and would
inflate the inferred-match path.
```

Over-matching raises too, so a pattern loosened until it "works" fails rather than
binding UTRs to wrong rows.

### Step 3 — Level 0, then Levels 1 and 3 (`509a905`, `a30d91e`)

**Level 0 (integrity), run first.** SPEC §5 declares Level 1 and Level 3 as 1:1 joins,
but `duplicate_ledger_entry` produces 1:2. A 1:1 join would dedup by accident and never
detect the fault it is graded on. `.first()`, `.one_or_none()` and `.find_one()` are
banned in the guarded packages by the linter.

**Counting is pinned**, because three numbers get called "duplicates" and only one is a
duplicate count:

| definition | value |
|---|---|
| groups (keys appearing more than once) | 106 |
| excess rows | 106 |
| group members | **212** |

The first two coincide *only* because the duplication factor is exactly 2 — which is how
an ambiguous count survives review. Level 0 emits **one finding per group**.

**Bank rows group by extracted UTR, never by narration.** Every `missing_utr` settlement
writes `NEFT/NA/RAZORPAY SETTLEMENT`, so narration grouping merges unrelated settlements
into a phantom group: 6 groups / 8 excess rows against a true 5 / 5.

**Findings carry their axis** — `LEDGER` selects the §7 R7 clause, `GAP` the R9 integrity
amendment.

**Level 3 tie-break policy:** with a UTR the match is *keyed*; without one, the fallback
searches unkeyed rows for one within **both** the amount tolerance and the date window.

```
exactly one candidate   -> INFERRED   (R4 sends it to HUMAN_REVIEW)
zero candidates         -> UNMATCHED_NO_CANDIDATE
more than one candidate -> UNMATCHED_AMBIGUOUS
```

Ambiguous is an **unmatched record**, never a pick. No nearest-neighbour, no
first-in-file, no lowest-id — any of those would make results depend on row order and the
evaluation irreproducible. A test shuffles payments, ledger and bank rows and asserts the
entire `MatchReport` is unchanged.

**Level 1 consumes Level 0 rather than re-deriving.** A test adds a synthetic Level 0
finding and confirms the payment flips to duplicate, then removes a real one and confirms
it flips back.

### Step 4 — the decomposer (`0f5d976`)

Sign convention, stated once: every cause is a signed contribution to

```
gap = actual_bank_credit - expected_at_CONTRACTED_rates
```

§6 step 1 makes the contract the baseline. Because every cause is measured against that
same baseline rather than against the running residual, the decomposition is
**order-independent**.

**Closure invariant:** `sum(attributed) + unexplained == gap`, exactly, in integer paise.
`decompose()` raises; `decompose_all()` catches at the **settlement boundary** and emits
`failed=True`, routing to UNRESOLVED and incrementing a metrics line — a run that dies
mid-batch produces no metrics at all.

Two attribution metrics were added because the 5×5 matrix does not test the product's
actual claim:

- `exact_cause_set_match_rate` — **set equality**, so naming fee drift plus a phantom
  adjustment is wrong, not half right.
- `rupee_attribution_error` — absolute misattribution per cause plus error on the
  unexplained residual, so "right cause, wrong size" is visible.

### Steps 5 and 6 — cascade, confidence, metrics (`125371b`)

The R1–R10 cascade, first match wins:

| # | State | Condition |
|---|---|---|
| R1 | UNRESOLVED | no counterpart on either side |
| R2 | AWAITING_BANK | settled, no bank credit, `as_of <= due` |
| R3 | UNRESOLVED | settled, no bank credit, past T+2 |
| R4 | HUMAN_REVIEW | match was inferred rather than keyed |
| R5 | UNRESOLVED | `abs(unexplained) >= MATERIALITY_PAISE` |
| R6 | HUMAN_REVIEW | `0 < abs(unexplained) < MATERIALITY_PAISE` |
| R7 | HUMAN_REVIEW | `ledger_agrees` false, **or a Level 0 integrity defect** |
| R8 | VERIFIED | keyed, `abs(gap) <= TOLERANCE_PAISE`, no attributions, ledger agrees |
| R9 | EXPLAINED | `unexplained == 0`, ≥1 attribution, **no attributed cause is an integrity defect** |
| R10 | UNRESOLVED | fallback — asserts; unreachable |

R8 and R9 are disjoint on `len(attributed)`, not on ordering alone, so the "40 paise
inside a 100-paise tolerance" overlap closes independently of the cascade.
`DECOMPOSITION_FAILED` is handled *before* the cascade as an error path, because a failed
closure means `unexplained_amount` is unknown and R5/R6 cannot be evaluated honestly.

**Confidence (§8)** — nine checks, equal weights. **Inapplicable checks are excluded from
the denominator, not counted as passes**: an orphan bank credit scores 0.0 with 0
applicable checks, not 1.0. Confidence gates the **action**, never the label: a state
outside `{VERIFIED, EXPLAINED}` never auto-reconciles however high it scores.

---

## 5. Decisions taken, and why

### SPEC.md blanks filled

| Field | Decision | Reason |
|---|---|---|
| Frontend | deferred; static page only | step 7 is below the protected line |
| Backend | Python 3.12, stdlib only | steps 1–6 are a batch pipeline; no server needed |
| Database | SQLite (`sqlite3`) available; unused in 1–6 | `afc.core` may not import it — the firewall keeps core pure |
| LLM | Anthropic `claude-sonnet-5`, display-only | behind an interface with a null stub; runs with no API key |
| Razorpay test-mode credentials | **no** | step 9 cut |

### Deviations from SPEC.md, each approved explicitly

1. **Level 2 deleted.** Tautologically true because the generator builds settlements by
   grouping its own payments. Would have reported 100% while doing no work. The project
   does not claim a three-level matcher.
2. **Level 0 added.** Duplicate detection before matching; read as implied by §5 rather
   than new scope.
3. **Fault table cut 14 → 10** (plus compounds). Fourteen classes across 500 records
   gives cells with two or three instances; ten gives a defensible matrix. Dropped:
   `rounding_drift` (circular — the decomposer has a rounding bucket),
   `ledger_amount_mismatch`, `adjustment_unexplained`.
4. **§7 parameterised, not extended.** `bounded` and `Material` had no numeric
   definition, so two of five states could not be coded. Now `MATERIALITY_PAISE` and
   `TOLERANCE_PAISE`, with a stated precedence order.
5. **One §7 clause completed.** A keyed, bank-reconciling settlement with a duplicated
   ledger entry matched **zero rows**. R7 now also fires on a false `ledger_agrees`.
6. **Second §7 change: `duplicate_bank_credit` → HUMAN_REVIEW.** Read literally it is
   EXPLAINED, since the duplicate accounts for the whole gap. But EXPLAINED is
   auto-reconcilable and product rule 3 forbids silently reconciling uncertain money — a
   duplicated credit will very likely be clawed back. **Attributing a gap is not the same
   as the gap being benign.** Consequence: EXPLAINED is now sourced by `fee_rate_drift`
   alone, 28 units, so one diagonal cell rests on a single generator behaviour.
7. **"Held-out" renamed `sealed_eval`.** The name overclaimed: because generation is one
   code path parameterised only by seed, the split has an *identical fault distribution*
   to dev. It tests generalisation to unseen amounts, dates and refund incidence — not to
   an unseen fault mix.
8. **Same-axis compound pairs excluded at scoring, not at generation.** Keeping them for
   study and requiring the splits to differ by seed alone are only consistent if
   generation is identical and the exclusion happens later. 36 units, counted and
   reported.
9. **Step 9 cut.** No test-mode credentials, below the protected line.

### Modelling decision: ledger compared at the APPLIED rate

A merchant books what actually reached the bank, so `fee_rate_drift` stays a gap-axis
fault and is not also counted as a ledger disagreement. **The consequence is large and
deliberate: ledger disagreements are 31, not 130.** Booking against the contracted rate
would be a different model of the merchant, not a bug fix.

---

## 6. Architecture

```
afc/
  config.py              every constant + its justification (single source)
  money.py               integer-paise arithmetic; no float touches currency
  pipeline.py            orchestration (unguarded: may touch clock/storage)
  core/                  GUARDED — pure, deterministic, no I/O, no clock, no LLM
    models.py            normalized schema + RECON_UNIT definition
    calendar.py          working-day arithmetic for the T+2 window
    holidays.py          declared closed-world holiday table
    faults.py            the answer key: fault -> true state, + composition rule
    integrity.py         Level 0 — duplicate findings with their axis
    matching.py          Levels 1 and 3
    decompose.py         the difference decomposer + closure invariant
    confidence.py        deterministic confidence + routing
    decide.py            the SPEC 7 table, as data, in one place
  generate/              GUARDED — synthetic data
    plan.py              injection targets as data; DEFAULT and SHIFTED mixes
    generator.py         generate(seed, targets) -> Dataset
    emit.py              writes the three source views as CSV + manifest
  ingest/                GUARDED — normalization
    narration.py         UTR extraction with a coverage assertion
    loader.py            CSV -> NormalizedBatch
  metrics/               GUARDED — scoring
    confusion.py         5x5 matrix, per-state precision/recall
    attribution.py       exact_cause_set_match_rate, rupee_attribution_error
  llm/                   QUARANTINED — display-only prose
    types.py             AiNote — the only type LLM text may inhabit
    narrate.py           NullNarrator, GarbageNarrator
tools/
  check_imports.py       the firewall: import layering, clock ban, dedup ban
  run_metrics.py         one command -> metrics_summary.json
  census.py              fault census; fails under the 15-unit floor
  render_constants.py    generates the README constants table
  make_viewer_data.py    wraps the JSON for file:// viewing
docs/index.html          static results viewer
```

Guarded packages may not import `afc.llm`, any vendor SDK, or network/storage modules.
The rule is checked in CI before the tests run, so a layering violation fails the build
even if every test passes.

---

## 7. Data model

`RECON_UNIT` is the **unit of classification and the denominator of every reported
metric**: one per payment, one per orphan bank credit, one per payment-less settlement.
It exists because §7 says "every transaction lands in exactly one state" while
AWAITING_BANK is a property of a *settlement* and orphan-record a property of a *bank
row* — three grains a single confusion matrix cannot count without a declared unit.

Entities: `RateCard`, `Order`, `Payment`, `Refund`, `Settlement`, `BankRow`,
`LedgerEntry`, `GroundTruth` (unit grain), `SettlementTruth` (gap-axis grain).

`SettlementTruth` exists because unit-level truth mixes axes: a compound payment's
`rupee_impact` includes its ledger duplicate, so attribution cannot be scored against it.

Money is integer paise everywhere; rates are integer basis points.

---

## 8. The dataset

- **500 orders → 518 recon units** (500 payments + 18 orphan bank credits)
- **1 Jun – 31 Jul 2026**, frozen `AS_OF = 2026-07-31` — chosen so the final three
  working days sit inside their T+2 window and host the `timing_lag` settlements
- 43 settlements, 606 ledger rows, 59 bank rows, 55 refunds

### Splits

| Split | Seed | Purpose |
|---|---|---|
| dev | 42 | tuned against freely |
| `sealed_eval` | 1337 | **run once**, at commit `125371b`, clean tree, nothing changed after seeing dev |
| distribution shift | 2027 (burned) | secondary; reweighted mix |
| reserve | 3119 | unopened |

**Reserve seeds burned: 1.** `sealed_eval` runs: **1**.

### Census (identical across dev and `sealed_eval` by construction)

| State | units | share | fault-only | share |
|---|---|---|---|---|
| VERIFIED | 172 | 35.7% | 0 | 0.0% |
| EXPLAINED | 28 | 5.8% | 28 | 9.0% |
| AWAITING_BANK | 31 | 6.4% | 31 | 10.0% |
| HUMAN_REVIEW | 155 | 32.2% | 155 | 50.0% |
| UNRESOLVED | 96 | 19.9% | 96 | 31.0% |

Every scored fault class clears a **15-unit floor**; `make census` fails the build if one
does not.

### The 10 fault classes

`fee_rate_drift`, `missing_utr`, `bank_credit_missing`, `bank_amount_mismatch` (injected
at both material and bounded magnitudes), `refund_not_in_ledger`,
`duplicate_ledger_entry`, `duplicate_bank_credit`, `orphan_bank_credit`, `timing_lag`,
`timing_overdue`, plus 8 cross-axis compound pairs and 3 same-axis pairs held to dev.

---

## 9. Metrics reported

`metrics_summary.json`, split into `metrics` (what a garbage LLM must not change) and
`run_meta` (wall clock, throughput, git sha — which a stub legitimately would).

The `metrics` block carries: the denominator and scored population; the 36 excluded units
**with the reason**; the 4 unkeyable bank rows; `decomposition_failures`; the full 5×5
matrix with per-state precision/recall/F1; a **fault-carrying-only** matrix; both
attribution metrics; coverage; the four rupee buckets including cost of false positives;
and the honesty clause verbatim.

---

## 10. Test inventory — 257 tests

| File | Tests | Covers |
|---|---|---|
| `test_faults.py` | 56 | answer-key invariants, composition rule, magnitude split |
| `test_calendar.py` | 37 | Saturdays, holidays, T+2 boundaries, a full-year sweep |
| `test_money.py` | 25 | the banker's-rounding trap, negatives, parsing, float rejection |
| `test_generator.py` | 22 | determinism, 500 payments, clean gap exactly 0, fault placement |
| `test_decompose.py` | 20 | closure, order-independence, no invented causes, scoring |
| `test_decide_and_metrics.py` | 19 | every cascade rule, confidence, **the §3 firewall test** |
| `test_integrity.py` | 19 | derived counts, the narration trap, axis routing |
| `test_matching.py` | 19 | outcomes vs truth, tie-break, order-independence, L0 mutation |
| `test_check_imports.py` | 17 | the firewall biting on each violation class |
| `test_ingest.py` | 14 | exact round trip, UTR coverage shortfall and over-match |
| `test_config.py` | 9 | every constant justified, honesty clause verbatim |

---

## 11. Commands

```bash
make metrics     # the demo path: dev split -> metrics_summary.json
make viewer      # metrics + docs/metrics_data.js, then open docs/index.html
make setup       # dev tooling: pytest + ruff (the pipeline needs neither)
make check       # ruff -> firewall -> README sync -> census -> 257 tests
make census      # fault census; fails under the 15-unit floor

python3 tools/run_metrics.py sealed_eval    # the sealed evaluation split
python3 tools/run_metrics.py shift          # distribution shift, secondary
```

---

## 12. Why the numbers should not be trusted at face value

Five limits, stated beside the accuracy number rather than in a methodology section.

1. **Generator and classifier share the same fee arithmetic.** Single-cause faults are
   correct by construction.
2. **The decomposer's cause list is the same closed set as the generator's fault list.**
   There is no cause it can fail to recognise, so `unexplained_amount` is near-zero by
   construction and "unknown is a valid answer" is *demonstrated* rather than tested.
3. **~64% of scored units carry a fault**, against roughly 5% in a real merchant file.
   The density makes every class measurable at the 15-unit floor; it also makes the
   number non-comparable to production.
4. **HUMAN_REVIEW is structurally flattered** — it holds 50% of fault-carrying units, so
   its precision and recall are inflated by base rate.
5. **The answer key's independence is unverifiable by construction.** The R1–R10 cascade
   was authored **before** the mapping and was in context throughout writing it. Three
   contamination traces are identified in FINDINGS.md, including the mapping adopting the
   cascade's exact `MATERIALITY_PAISE` constant where §7 gives only the word "material".
   What independence survives sits in *compound precedence*, not in the single-fault
   mappings that produce the diagonal cells. **A zero-diff between answer key and
   classifier is therefore not validation** — it is the expected outcome.

### Fixture-tested, never exercised by batch data

| Path | Why it never fires |
|---|---|
| `TOLERANCE_PAISE` in R8 | a clean gap is exactly 0, and every injected fault clears ₹1 by an order of magnitude |
| `UNMATCHED_AMBIGUOUS` | all 4 `missing_utr` settlements resolve to exactly one candidate |
| `refund_variance` | refunds are consistent between Razorpay and bank |
| `adjustment_variance` | `adjustment_unexplained` was cut; no adjustment rows exist |
| `rounding_variance` | the residual is exactly 0 — generator and classifier compute per-payment net identically |

The closure invariant is **algebraically guaranteed** by the current implementation and
cannot fire on today's code. It guards future edits, not this run.

---

## 13. Defects caught — see FINDINGS.md

23 logged, each with what was wrong, how it was found, what it would have done to the
reported number, and the fixing commit. Tagged `[spec]` for defects in SPEC.md as
written, `[impl]` for implementation, `[mine]` for my own errors.

A representative sample:

| # | Defect | Would have caused |
|---|---|---|
| 1 | §6's worked example is arithmetically impossible (19× gross mismatch; rounding 8.8× the theoretical max) | a decomposer calibrated to impossible inputs |
| 5 | §7 matched **zero rows** for a bank-reconciling settlement with a duplicated ledger entry | 137 units with no state |
| 8 | `round(gross * mdr_rate)` wrong two ways, rate-dependent | silent 1-paise drift into the rounding bucket it is scored against |
| 10 | Level 2 tautological | 100% reported while doing no work |
| 15 | bank rows grouped by narration invent a phantom duplicate (6/8 vs true 5/5) | 4 settlements to HUMAN_REVIEW, reading as a classifier result |
| 16 | "106 duplicates" ambiguous; "16 bank duplicates" simply wrong | double-counting into HUMAN_REVIEW |
| 20 | fault-only matrix paired true states with the wrong fault classes | a headline breakdown computed over an arbitrary subset |
| 21 | the §3 firewall test would have passed vacuously | the strongest architectural claim resting on a test that could not fail |
| 22 | `make metrics` failed on a fresh clone | the entire demo path dead at step one |

---

## 14. Verification pass — 12 of 13

| Check | Result |
|---|---|
| Fresh clone → `make metrics`, twice | **PASS** both times |
| Determinism (two runs, diff excl. `run_meta`) | **PASS** — `metrics` sha256 identical |
| Firewall bites: `afc.llm` import / `.first()` / `datetime.now()` | **PASS** all three, each naming the right spec section |
| Required metrics lines | 12/13 → **fixed** (finding 23: exclusion reason added) |
| `make check` from a clean venv | **PASS** — 257 tests, no warnings |
| README first 35 lines | FINDINGS link at line 13, all four limits by line 33 |

---

## 15. Known open items

- **Steps 7–10 incomplete.** No filterable exception table, no truth graph, no copilot.
- **`docs/index.html` `file://` verification is incomplete.** It renders correctly when
  served over HTTP (verified end-to-end, no console errors, honesty-clause expand
  reconstitutes the full clause exactly). The `file://` path relies on a same-directory
  `<script src="metrics_data.js">`, which browsers permit where `fetch()` is blocked, but
  the tooling available here could not load a `file://` URL to confirm end-to-end.
  Verify by hand with `open docs/index.html`.
- **The evaluation split shares a fault distribution with dev**, so it does not test
  robustness to a different fault mix. The distribution-shift split partly covers this
  and is reported as an aggregate only.
- **Attribution scores 1.0000 with 0 paise of error** because the cause list is closed.
  The first honest signal would come from a real settlement file.

---

## 16. Commit history

```
a0582a3  Scaffold: package layout and decisions recorded in SPEC.md
833b69d  LLM firewall and clock ban, enforced by an AST linter in CI
42a1521  Integer-paise arithmetic, justified constants, README
d9b6899  Working-day calendar for the T+2 settlement window
5c65036  Ground truth: fault -> true state, committed before the classifier exists
ea5867f  Synthetic generator, normalized schema, and the fault census
4f5c9fc  Rename held-out to sealed_eval; add a distribution-shift split
de03522  Normalization: three source files into the common schema
509a905  Level 0: duplicate integrity findings, counted and routed correctly
a30d91e  Levels 1 and 3: payment axis and settlement-to-bank axis
0f5d976  Difference decomposer, and metrics that score attribution
94a137d  FINDINGS.md: the defect ledger, linked under the accuracy number
125371b  Steps 5 and 6: SPEC 7 cascade, confidence, and the metrics harness
bf1c208  Make the demo path work on a fresh clone
69c2bf3  sealed_eval result, README for a judge with thirty seconds
fc900d9  FINDINGS 22: the demo path did not work on a fresh clone
7886be2  Exclusion reason in metrics JSON; FINDINGS above the fold
29b5437  Static results viewer at docs/index.html
```
