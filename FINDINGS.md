# Findings

Every real defect this process caught, in the order caught.

This file exists because the metrics will read as perfect, and they should not be
trusted on that basis. The generator and the classifier share fee arithmetic; the
decomposer's cause list is the same closed set as the generator's fault list; the
answer key and the classifier share an author. Under those conditions a page of 100%
scores is what you would expect from a system that works *and* from one that is
merely self-consistent, and the scores cannot tell you which.

The defects below can. They are the evidence of rigour that the scores are not.

Sources are marked **[spec]** for defects in `SPEC.md` as written, **[impl]** for
defects in the implementation, and **[mine]** for errors I made and corrected.

---

### 1. The §6 worked example is arithmetically impossible — **[spec]**

**What was wrong.** The example JSON attributes 38,800 paise of fee drift to "MDR 2.1%
applied vs contracted 1.9% across 47 payments", against an `expected` of 987,412 paise.
A 0.2% rate delta producing 38,800 paise implies ~₹194,000 of gross; an
`expected_bank_credit` of ₹9,874 implies ~₹10,101 of gross. The two differ by 19×.
Separately, 412 paise of rounding across 47 payments exceeds the theoretical maximum of
~47 paise (half a paise per rounding, two per payment) by 8.8×.

**How it was found.** Working the example numerically instead of reading it. The outer
arithmetic is self-consistent — `gap = expected − actual`, and the attributions sum to
the gap — which is why it survives inspection. Only the *proofs* are impossible.

**What it would have done.** The example is the obvious first test fixture for the
decomposer. Building against it would have produced a decomposer calibrated to
impossible inputs, and a rounding bucket sized ~9× too large — which would then have
absorbed real unexplained amounts and understated `unexplained_amount`, the one number
§6 insists must never be fabricated.

**Fixed in.** No code was built against it. The correct arithmetic and the real
rounding bound (1.09 paise per payment) are in `0f5d976`.

---

### 2. The unit of classification was undefined — **[spec]**

**What was wrong.** §7 says "every transaction lands in exactly one state" and §9 says
to classify "each transaction", but `AWAITING_BANK` is a property of a *settlement*,
orphan-record is a property of a *bank row*, and the ledger clauses are properties of a
*payment*. Three grains, one confusion matrix, no declared denominator.

**How it was found.** Trying to state what a row of the 5×5 matrix would be.

**What it would have done.** The matrix would have had an undefined denominator and
silently mixed grains — 500 payments against ~43 settlements against 18 bank rows.
Every percentage would have been uninterpretable, and the error would not surface as a
failure anywhere.

**Fixed in.** `ea5867f` — one `recon_unit` per payment, per orphan bank credit, per
payment-less settlement; 518 units, denominator printed at the top of the metrics.

---

### 3. `AWAITING_BANK` depended on the wall clock — **[spec]**

**What was wrong.** `timing_lag` and `timing_overdue` are defined relative to "now", and
`AWAITING_BANK` vs `UNRESOLVED` turns on whether a settlement is inside its T+2 window.
Nothing in the spec froze that reference point.

**How it was found.** Asking what "now" means for a fixed-seed dataset.

**What it would have done.** The same dataset would reclassify overnight. A record
measured as `AWAITING_BANK` on Tuesday becomes `UNRESOLVED` on Thursday, so the
sealed-eval confusion matrix would not reproduce — and the fixed-seed guarantee in §12
would be false while appearing to hold.

**Fixed in.** `ea5867f` (frozen `AS_OF = 2026-07-31`) and `833b69d` (the linter bans
`datetime.now()`, `date.today()`, `time.time()` in every guarded package).

---

### 4. Ground truth recorded causes, but the matrix needs states — **[spec]**

**What was wrong.** §4 says the generator records "its true cause and true rupee
impact". §9 demands a 5×5 confusion matrix, which requires true *states*. The
fault→state mapping is a required artifact the spec never mentions.

**How it was found.** Asking what the matrix would be scored against.

**What it would have done.** The mapping would have been written after the classifier,
by reading it — making the matrix a test of whether the classifier agrees with itself.

**Fixed in.** `5c65036`, committed before `decide.py` existed. See finding 17 for why
that ordering proves less than it appears to.

---

### 5. The §7 decision table was not total — ledger faults matched zero rows — **[spec]**

**What was wrong.** A settlement keyed at both levels, reconciling to the bank exactly,
carrying a duplicated ledger entry, matches **no row** of the §7 table. `VERIFIED`
requires "ledger agrees" (fails). `EXPLAINED` requires `unexplained_amount == 0` with
amounts differing (they don't). `HUMAN_REVIEW` requires `unexplained_amount > 0` (it is
0). `UNRESOLVED` requires a material gap (there is none).

**How it was found.** Building the truth table and searching for inputs satisfying zero
rows.

**What it would have done.** 137 units — every `duplicate_ledger_entry` and
`refund_not_in_ledger` record — would have fallen through the table. The implementer
would have invented a state at the keyboard, which is exactly what §7 forbids.

**Fixed in.** `5c65036` — `HUMAN_REVIEW` now also fires when `ledger_agrees` is false
while the settlement↔bank axis reconciles. Framed as *completing* a predicate §7 already
names, not adding one.

---

### 6. The §7 table was not mutually exclusive — **[spec]**

**What was wrong.** A gap of 40 paise against a 100-paise tolerance is simultaneously
"amounts equal within tolerance" (`VERIFIED`) and "amounts differ" (`EXPLAINED`). No
precedence is stated anywhere in the table.

**What it would have done.** State assignment would depend on evaluation order — an
implementation detail, not a decision — so two correct implementations would disagree.

**Fixed in.** `5c65036` — explicit R1–R10 precedence, first match wins, and R8/R9 made
disjoint on `len(attributed)` so the overlap closes independently of ordering.

---

### 7. `bounded` and `Material` had no numeric definition — **[spec]**

**What was wrong.** `HUMAN_REVIEW` requires `unexplained_amount > 0` "but bounded";
`UNRESOLVED` requires a "material gap". Two of five states could not be coded as
written, while §7 simultaneously forbids inventing conditions.

**Fixed in.** `5c65036` — `MATERIALITY_PAISE` and `TOLERANCE_PAISE` as named constants
with justifications in the README, framed as parameterising §7's own words rather than
adding conditions.

---

### 8. `round(gross * mdr_rate)` is wrong in two different ways — **[spec]**

**What was wrong.** SPEC §4 specifies neither a rounding mode nor a numeric type.

```
1500 paise @ 1.90%  ->  1500*0.019 is EXACTLY 28.5 in binary64
                        Python round() is banker's, ties to even -> 28
                        finance rounds halves away from zero      -> 29
1500 paise @ 2.10%  ->  1500*0.021 is 31.500000000000004, not the tie
                        round() is then accidentally correct      -> 32
```

**How it was found.** Running the arithmetic across rates rather than reasoning about it.

**What it would have done.** A silent one-paise error per payment, rate-dependent so it
survives casual testing, accumulating across a settlement into *exactly* the
`rounding_variance` bucket — indistinguishable from the injected rounding fault it would
be scored against.

**Fixed in.** `42a1521` — integer basis points, halves away from zero, floats rejected
with `TypeError` at the arithmetic boundary.

---

### 9. The mandated firewall test contradicts itself — **[spec]**

**What was wrong.** §3 requires every reported metric to be *unchanged* when the LLM
returns garbage, and mandates a test that diffs the metrics output. §9 lists throughput
— wall-clock seconds — as a reported metric. A garbage stub is faster than a real call,
so the mandated test fails against a perfectly firewalled system.

**Fixed in.** `833b69d` — `metrics_summary.json` splits into `metrics` (diffed) and
`run_meta` (timing, git sha, model; excluded). The LLM never runs inside the timed path.

---

### 10. Level 2 is tautological — **[spec]**

**What was wrong.** §5 Level 2 checks that a settlement's payment count and gross total
equal the sum of its constituent payments. The generator *builds* settlements by
grouping its own payments, so the check cannot fail, and no fault in the table can break
it.

**What it would have done.** Reported 100% while doing no work, and the project would
have claimed a three-level matcher on the strength of a level that tests nothing.

**Fixed in.** `a30d91e` — deleted. The README states that this project ships Level 0
integrity plus two matching levels and does not claim three.

---

### 11. `AS_OF` left only one working day for `AWAITING_BANK` — **[impl]**

**What was wrong.** With `AS_OF = 2026-08-03` and a T+2 window, only settlements dated
31 July qualified as still-awaiting — one settlement day for 31 required `timing_lag`
units.

**How it was found.** The generator raised on the date assignment before any test ran.

**Fixed in.** `ea5867f` — `AS_OF = 2026-07-31`, opening the final three working days.

---

### 12. The clean-settlement test excluded the settlements it needed — **[mine]**

**What was wrong.** The test proving a clean settlement's gap is exactly 0 filtered out
any settlement containing *any* faulty unit. It reported "only 0 fully clean settlements
to check" and failed.

**Why it mattered.** Ledger-axis faults do not disturb settlement↔bank arithmetic at
all — that is precisely what the axis rule says. The filter had to *be* the axis rule.

**What it would have done.** The claim underpinning "`TOLERANCE_PAISE` never binds" — a
clean gap is exactly 0, not merely within a rupee — would have shipped untested.

**Fixed in.** `ea5867f` — 17 bank-clean settlements now verified at exactly 0.

---

### 13. "Held-out" overclaimed what the split tests — **[impl]**

**What was wrong.** Because generation is one code path parameterised only by seed, the
evaluation split has an *identical fault distribution* to dev. Calling it held-out
implies robustness to an unseen fault mix, which it does not test.

**What it would have done.** A reader would have taken the headline number as evidence
of generalisation it does not support.

**Fixed in.** `4f5c9fc` — renamed `sealed_eval`, with the definition travelling in code,
README and manifest, plus a distribution-shift split that does vary the mix.

---

### 14. A plausible UTR regex drops 33% of rows silently — **[impl]**

**What was wrong.** Assuming the reference is digits after the prefix is true of
settlement UTRs and false of orphan-credit references. Coverage: 37/55.

**What it would have done.** 18 rows arrive with blank UTRs, which is indistinguishable
from injected `missing_utr` faults. The inferred-match path and `HUMAN_REVIEW` both
inflate, no exception is raised, and the metrics look plausible.

**Fixed in.** `de03522` — extraction coverage asserted against the manifest's known UTR
count; both shortfall *and* over-match raise.

---

### 15. Grouping bank rows by narration invents a duplicate that does not exist — **[impl]**

**What was wrong.** Every `missing_utr` settlement writes the same narration text,
`NEFT/NA/RAZORPAY SETTLEMENT`. Grouping on narration merges four unrelated settlements
into one phantom duplicate group: **6 groups and 8 excess rows against a true 5 and 5.**

**How it was found.** Deriving the expected bank-duplicate count instead of asserting it
— prompted by the owner challenging a number I had stated.

**What it would have done.** All four `missing_utr` settlements routed to
`HUMAN_REVIEW` for a duplicated credit that does not exist. The error would have
appeared in the confusion matrix as a classifier result rather than a grouping bug, and
`missing_utr` recall would have looked like a modelling problem.

**Fixed in.** `509a905` — bank rows group by *extracted UTR*; unkeyable rows are
reported as `unkeyed_bank_rows`, not dropped.

---

### 16. Two ways to count duplicates, one of which is not a duplicate count — **[mine]**

**What was wrong.** I stated Level 0 should find "106 duplicated ledger rows and 16
duplicated bank credits". Both were unsafe:

- **106** is ambiguous. Groups = 106, excess rows = 106, group members = **212**. The
  first two coincide *only* because the duplication factor is exactly 2.
- **16** was simply wrong — a unit target, not a row count. `duplicate_bank_credit` is
  settlement-level: 55 units inherit it across **5 settlements**.

**What it would have done.** Flagging group members would double-count, pulling clean
records into `HUMAN_REVIEW` via the R7 ledger clause — again reading as a classifier
result rather than a counting bug. A hardcoded `16` in a test would have passed or
failed for reasons unrelated to the logic.

**Fixed in.** `509a905` — all three counts exposed separately; expectations derived from
the injection plan *and* from ground truth independently, never as literals.

---

### 17. `EXPLAINED` conflated "attributed" with "benign" — **[spec]**

**What was wrong.** Read literally, a duplicated bank credit is `EXPLAINED`: the
duplicate accounts for the whole gap, so `unexplained_amount` is 0. But `EXPLAINED` is
auto-reconcilable, and a duplicated credit is money that will very likely be clawed
back. Product rule 3 forbids silently reconciling uncertain money.

**Fixed in.** `5c65036` — R9 now requires that no attributed cause is itself an
integrity defect, and R7 routes Level 0 duplicate findings to `HUMAN_REVIEW`.
**Consequence, stated:** `EXPLAINED` is now sourced by `fee_rate_drift` alone — 28 units
— so one diagonal cell rests on a single generator behaviour.

---

### 18. My calendar demo proved nothing on the day I chose — **[mine]**

**What was wrong.** I set out to show a Friday settlement not flagged overdue on the
following Saturday. Both the correct calendar *and* naive T+2-calendar-days say
"awaiting" on that Saturday — Friday + 2 calendar days is Sunday. The chosen day does
not discriminate.

**What it would have done.** A demonstration that "passes" while testing nothing, and a
calendar shipped on the strength of it.

**Fixed in.** `d9b6899` — the check sweeps past the Saturday; naive breaks on the
Monday, reporting overdue after zero working days.

---

### 19. The linter blamed the wrong spec section — **[impl]**

**What was wrong.** Every violation printed "SPEC 3: no LLM output may be read by..."
including `.first()` dedup violations, which have nothing to do with the LLM firewall.

**Fixed in.** `509a905` — the footer now names the rule actually broken.

---

### 20. The fault-only matrix paired true states with the wrong fault classes — **[mine]**

**What was wrong.** The fault-carrying-only confusion matrix was built by zipping
`scored_pairs` (built in dictionary insertion order) against a separately `sorted()`
list of ground-truth entries. The two orderings do not correspond, so each true/predicted
pair was filtered using some *other* record's fault class.

**How it was found.** Reading the harness back before reporting its output, and asking
what guaranteed the two sequences aligned. Nothing did.

**What it would have done.** The fault-only breakdown — the number that exists precisely
because a headline dominated by clean records measures the easy case — would have been
computed over an arbitrary subset. With a perfect diagonal it would still have read
100%, so nothing would have looked wrong.

**Fixed in.** `HEAD` — the fault class is carried alongside each pair rather than
re-derived, and a test asserts `VERIFIED` support is 0 in the fault-only matrix, which
is false under the bug.

---

### 21. The mandated firewall test would have passed vacuously — **[impl]**

**What was wrong.** §3 requires a test that stubs the LLM with garbage and asserts the
metrics are unchanged. With no LLM call anywhere in the pipeline, that test passes
trivially and demonstrates nothing.

**How it was found.** Asking what would have to break for the test to fail.

**What it would have done.** The strongest architectural claim in the project — no
metric depends on LLM output — would have rested on a test that could not fail.

**Fixed in.** `HEAD` — a narrator now runs over the finished metrics and writes an
`AiNote` into the summary. Two differently-seeded garbage narrators must leave the
`metrics` block byte-identical *and* produce different notes; the second assertion is
what makes the first mean something.

---

## On the independence of the answer key

Finding 4 records that the fault→state mapping was committed before `decide.py` existed.
That ordering is real and it is in the git history, but it proves less than it appears
to, and the gap is worth stating in full rather than leaving as a caveat.

**The git-ordering argument proves the mapping predates the *implementation*, not the
*design*.** The R1–R10 cascade was authored into the README **before** `faults.py` was
written. So the cascade was in context throughout the writing of the mapping, and the
claim that the mapping was authored "from the SPEC 4 fault definitions and the SPEC 7
table alone" is **unverifiable by construction** — it could not have been unseen.

Three specific contamination traces are visible in the mapping file itself:

1. **`bank_amount_mismatch` → `BY_MAGNITUDE`, keyed to `MATERIALITY_PAISE`.** §7's plain
   text says "material" and "bounded" with no numbers attached. The mapping adopting the
   *exact constant* the cascade uses at R5/R6 is not derivable from §7. It was read off
   the cascade.
2. **`bank_credit_missing`'s stated reason** — "generated past the T+2 window so it is
   not a timing case" — is operational reasoning from the cascade's R2/R3 split. §4's
   one-line fault description contains nothing about window position.
3. **The axis taxonomy maps almost one-to-one onto cascade rows**: gap→R5/R6,
   ledger→R7, presence→R2/R3, keying→R4. That correspondence is unlikely to be
   independent invention.

**What survives as genuine independence is thin.** The axis rule excluded
`fee_rate_drift + duplicate_bank_credit` before the decomposer existed, and one compound
disagreement stands on the record (`timing_lag + duplicate_ledger_entry`, where severity
ordering and the axis reading differ). But note *where* that independence sits: in
**compound precedence**, not in the single-fault mappings. **The single-fault mappings
are exactly what produce the diagonal cells of the confusion matrix, and they show no
independent signal at all.**

**Therefore a zero-diff between mapping and cascade is not validation.** It is the
expected outcome of one author designing the cascade and then writing an answer key with
that cascade in mind, and it should be read as consistent with the shared-author limit
rather than as evidence the classifier is correct. A small non-zero diff would carry more
information than a zero one, because it would demonstrate that at least some of the key
was derived from the spec rather than from the implementation.

The honest remedy is not available inside this project: it requires a second author, or
a real settlement file whose faults neither list anticipated.

---

## What is still open

Not defects, but limits that no amount of further work on this dataset can remove.
They are listed beside the accuracy number in the README, and repeated here because a
findings list that only contains solved problems is its own kind of dishonesty.

- Attribution scores 1.0000 with 0 paise of error because the decomposer's cause list is
  the same closed set as the generator's fault list. There is no cause it can fail to
  recognise.
- The closure invariant is algebraically guaranteed by the current implementation and
  cannot fire on today's code. It guards future edits, not this run.
- Five code paths are fixture-tested but never exercised by batch data:
  `TOLERANCE_PAISE`, `UNMATCHED_AMBIGUOUS`, `refund_variance`, `adjustment_variance`,
  `rounding_variance`.
- ~64% of scored units carry a fault, against roughly 5% in a real merchant file.
- The answer key and the classifier share an author.
