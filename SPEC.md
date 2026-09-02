# AI Finance Controller — Project Spec

**Tagline:** Every rupee gets a verified trail.

**Hackathon:** Razorpay Buildathon, Track 04 (AI Finance Controller)

**The bar we are graded on:** throughput + measured accuracy + an honest exception list, across a 50+ record batch. One cherry-picked match proves nothing.

---

## FILL THIS IN BEFORE STARTING

- Deadline: 4 september 2026
- Frontend preference (default: Next.js + Tailwind): deferred — step 7 is below the
  protected line. Steps 1-6 emit `metrics_summary.json` and an exception list; a bare
  static page reads them if there is time. No build step is being taken on.
- Backend preference (default: FastAPI + Python): Python 3.12, stdlib only. Steps 1-6
  are a batch pipeline with no server, so no web framework is a dependency yet.
- Database (default: SQLite, file-based, zero setup): SQLite via stdlib `sqlite3`,
  file-based, zero setup. `afc.core` may not import it (firewall keeps core pure).
- LLM provider/model: Anthropic `claude-sonnet-5`, display-only prose. Behind an
  interface with a null stub, so the whole pipeline runs with no API key.
- Do we have Razorpay test-mode credentials? (yes/no): **no**. Step 9 (test-mode
  adapter) is cut; it was below the protected line and the deadline decided it.

---

## 1. WHAT THIS IS

Razorpay already tells a merchant what happened inside its own payment infrastructure: payments, settlements, fees, tax, UTRs, and a Settlement Reconciliation Report.

What Razorpay does not do:

1. Check its own records against the merchant's **bank statement** and **accounting ledger**
2. **Explain** why two numbers differ, by naming the specific deduction that caused it
3. Attach a **confidence** to any conclusion, or route uncertain cases to a human

Those three gaps are the product.

> Razorpay tells you where the money went. We prove that the books agree.

## 2. SCOPE — READ THIS BEFORE ADDING ANYTHING

Three data sources only:

1. Razorpay (payments, orders, settlements, refunds, fees, tax, UTR)
2. Bank statement (CSV/Excel)
3. Merchant ledger (CSV/Excel)

**Explicitly out of scope. Do not build these:**

- Invoice OCR / PDF / image / ZIP ingestion
- GST or ITC eligibility logic, tax-opportunity detection, tax health score
- Any feature not on the priority list in §10

If invoices are needed for a matching story, accept them as CSV columns only. Tax rules change and getting them subtly wrong in front of finance judges is worse than not shipping them.

## 3. THE ONE ARCHITECTURAL RULE

The LLM is not the financial source of truth.

```
question / exception
  -> deterministic DB queries
  -> deterministic financial arithmetic
  -> evidence object
  -> LLM writes prose about the evidence
  -> response
```

**Hard constraint, testable:** no LLM output may be read by any classification, scoring, matching, or arithmetic code path. LLM text lands in a display-only field tagged `ai_generated: true`. **If every LLM call returned nonsense, every reported metric must be unchanged.** Write a test that asserts this by stubbing the LLM with a garbage generator and diffing the metrics output.

Match on numbers and dates, not fuzzy strings. In settlement data amounts drift by paise and dates drift by days, but nobody misspells an order ID.

## 4. SYNTHETIC DATA GENERATOR — BUILD THIS FIRST

Fixed random seed. No real merchant data, no PII, no credentials in the repo.

Generate **500 orders** producing consistent Razorpay, bank, and ledger views, then inject faults with recorded ground-truth labels.

Money is stored in **paise as integers**. Never floats. Compare with explicit tolerance.

### Settlement arithmetic

One bank credit covers many payments, netted. Per payment:

```
fee            = round(gross * mdr_rate)
gst_on_fee     = round(fee * 0.18)
net_per_payment = gross - fee - gst_on_fee
```

Per settlement:

```
expected_bank_credit = sum(net_per_payment for payments in settlement)
                       - refunds_in_cycle
                       + adjustments
```

GST is charged on the **fee**, not on the transaction amount. Settlement cycle is T+2 **working days**, where working days exclude Sundays, bank holidays, and the second and fourth Saturday of each month. Implement this calendar properly — it prevents the classic false positive of flagging a Friday payment as missing on Saturday.

### Faults to inject

Each labeled with its true cause and true rupee impact.

| Fault | Description |
|---|---|
| `fee_rate_drift` | MDR silently changes mid-dataset (e.g. 1.9% -> 2.1%) |
| `missing_utr` | Settlement processed, UTR field blank |
| `bank_credit_missing` | Settlement exists, no matching bank row at all |
| `bank_amount_mismatch` | Bank credit differs from expected net |
| `refund_not_in_ledger` | Refund deducted by Razorpay, absent from books |
| `duplicate_ledger_entry` | Same payment booked twice |
| `duplicate_bank_credit` | Same UTR appears twice in the statement |
| `timing_lag` | Settlement legitimately not yet in bank, inside the T+2 window |
| `timing_overdue` | Settlement past the T+2 window with no bank credit |
| `rounding_drift` | Paise-level differences accumulating across a settlement |
| `ledger_amount_mismatch` | Books record a different amount than the payment |
| `orphan_bank_credit` | Bank credit with no corresponding settlement |
| `adjustment_unexplained` | Adjustment line with no supporting record |
| `compound` | **Two or more of the above on the same transaction** |

Compound faults matter. Real settlement files contain far more of them than single-cause datasets do, and reporting them separately is a credibility signal.

### Split

- **Dev set:** used freely while building
- **Held-out set:** generated with a different seed, never inspected, never tuned against, run once at the end

## 5. MATCHING — THREE LEVELS, NOT ONE

This is where most implementations go wrong. Do not build a single flat row-to-row matcher.

**Level 1 — payment <-> order <-> ledger entry (1:1)**
Join on `payment_id` / `order_id`. Amount must agree exactly.

**Level 2 — payments <-> settlement (N:1)**
Group payments by `settlement_id`. The settlement's payment count and gross total must equal the sum of its constituent payments.

**Level 3 — settlement <-> bank credit (1:1 row, N:1 arithmetic)**
Join on UTR. The **amount check is an aggregation**: does `expected_bank_credit` (computed from all payments in that settlement) equal the single bank credit? When UTR is missing, fall back to amount-within-tolerance plus date-within-window, and mark the match as inferred rather than keyed.

Level 3 is where the interesting exceptions live.

## 6. DIFFERENCE DECOMPOSER

When Level 3 fails, do not stop at "mismatch." Decompose the gap into named, arithmetic-proven causes and attribute rupees to each:

1. Recompute expected fee and GST-on-fee from contracted rates
2. Compare against fees actually deducted -> `fee_variance`
3. Check refunds in the cycle -> `refund_variance`
4. Check adjustments -> `adjustment_variance`
5. Check for duplicate credits -> `duplicate_variance`
6. Accumulate paise rounding -> `rounding_variance`
7. Whatever remains is `unexplained_amount`

Output shape per exception:

```json
{
  "settlement_id": "setl_XXX",
  "expected": 987412,
  "actual": 948200,
  "gap": 39212,
  "attributed": [
    {"cause": "fee_rate_drift", "amount": 38800, "proof": "MDR 2.1% applied vs contracted 1.9% across 47 payments"},
    {"cause": "rounding", "amount": 412, "proof": "paise rounding across 47 payments"}
  ],
  "unexplained_amount": 0
}
```

If `unexplained_amount > 0`, say so plainly. **Never fabricate a cause.** Unknown is a valid, first-class answer.

## 7. DECISION TABLE

Every transaction lands in exactly one state. This table is the spec — implement it literally, do not invent conditions.

| State | Conditions |
|---|---|
| **VERIFIED** | Keyed match at all three levels, amounts equal within tolerance, ledger agrees |
| **EXPLAINED** | Amounts differ, but `unexplained_amount == 0` — every rupee attributed by arithmetic |
| **AWAITING_BANK** | Settlement processed, no bank credit, **inside** the T+2 working-day window |
| **HUMAN_REVIEW** | `unexplained_amount > 0` but bounded and evidence partially supports a cause; or match was inferred rather than keyed |
| **UNRESOLVED** | Material gap, no supporting evidence for any cause; or overdue past T+2 with no bank credit; or orphan record with no counterpart |

Tolerance is a named constant (suggest ±₹1.00 = 100 paise per settlement), not a magic number scattered through the code.

## 8. CONFIDENCE — DETERMINISTIC, NOT VIBES

Confidence is a pure function of which checks passed. It is never produced by an LLM.

Define a fixed checklist per transaction, each check contributing a fixed weight:

```
checks = [
  ("payment_found",        weight),
  ("order_id_matches",     weight),
  ("settlement_found",     weight),
  ("utr_present",          weight),
  ("utr_matches_bank",     weight),
  ("amount_within_tol",    weight),
  ("ledger_agrees",        weight),
  ("no_duplicate_found",   weight),
  ("gap_fully_attributed", weight),
]
confidence = sum(weights of passed checks) / sum(all weights)
```

Store the passed/failed list alongside the score so the UI can render evidence, and so you can answer "how is 42% computed?" with a straight answer.

Routing thresholds are **configurable constants, tuned on the dev set only**, then reported honestly:

- `>= AUTO_THRESHOLD` -> auto-reconcile
- `>= REVIEW_THRESHOLD` -> AI suggestion, human confirms
- below -> human review queue

Do not claim these thresholds are universally correct. Report what they were and what they cost on held-out data.

## 9. METRICS — WHAT AND HOW TO REPORT

**Primary task:** classify each transaction into one of the five states in §7.

Report a **full 5x5 confusion matrix** on the held-out set, plus per-state precision and recall. A bare "precision 98%" with no defined population is meaningless — say precision *of what*.

Also report:

- Throughput: records/second and total wall-clock for the batch
- Coverage: share of records reaching a terminal state without human input
- **Cost of false positives:** total rupees auto-reconciled that were actually wrong
- Rupees correctly auto-reconciled
- Rupees sent to human review
- Rupees unresolved
- Performance on **compound faults reported separately** from single-cause faults

### Mandatory honesty clause

This sentence must appear next to the accuracy number everywhere it is displayed — the UI, the README, and inside `metrics_summary.json`:

> The data generator and the classifier share the same fee arithmetic, so single-cause faults are correct by construction. This score demonstrates internal coherence, not that the system would survive a real settlement file.

Report whatever the engine actually produces, including bad results. Never hide failures. Never hardcode a target number.

## 10. BUILD PRIORITY

Build strictly in this order. Do not start step N+1 until N works and is tested.

| # | Deliverable | Done when |
|---|---|---|
| 1 | Synthetic generator with labeled faults | 500 records, dev + held-out splits, ground truth saved |
| 2 | Normalization to common schema | All three sources load into one table, paise integers |
| 3 | Three-level matcher (§5) | Level 3 aggregation works; unit tests pass |
| 4 | Difference decomposer (§6) | Attributes rupees to named causes with proofs |
| 5 | State machine + confidence + human queue (§7, §8) | Every record gets a state and an evidence list |
| 6 | Metrics harness (§9) | Confusion matrix on held-out set, one command |
| 7 | Dashboard + exception list | Batch summary, filterable exception table |
| 8 | Truth graph for a single transaction | Order -> payment -> settlement -> UTR -> bank -> ledger, per-edge status |
| 9 | Razorpay test-mode adapter | Proves schema maps to real API fields |
| 10 | Finance Copilot over verified results | Queries computed results only, never recalculates |

**Minimum shippable demo (steps 1-6 plus a bare UI).** If everything after step 6 gets cut, we still meet the track's bar. Protect steps 1-6 at all costs.

### On the Razorpay adapter

Before committing time here, verify from current Razorpay docs what test mode actually returns for settlements, UTRs, and the recon report. Test mode likely does not run a real T+2 cycle or produce real bank UTRs. If it cannot produce a realistic settlement cycle, the adapter is a **correctness demo proving our schema matches real API fields** — not the data path for the demo. Do not burn a day on it.

## 11. PRODUCT RULES

1. Never invent financial data
2. Never claim 100% automation
3. Never silently reconcile uncertain transactions
4. Every automated decision carries an evidence list
5. All financial arithmetic is deterministic
6. LLM prose is grounded in already-computed results and tagged as AI-generated
7. Unknown is a valid answer
8. Human review is a first-class workflow, not an error path
9. The system measures and publishes its own accuracy, including failures
10. The demo shows at least one clean reconciliation and one exception handled gracefully

## 12. CODE QUALITY

- Money as integer paise, everywhere. Never floats for currency.
- Unit tests for: fee arithmetic, GST-on-fee, working-day calendar, each of the three match levels, each decomposer branch, confidence scoring
- One test asserting the LLM firewall (§3): stub the LLM with garbage, assert metrics unchanged
- Fixed seeds so every run is reproducible
- Secrets in env vars only, `.env.example` committed, `.env` gitignored
- Synthetic data only. No real merchant data, no PII, no live API calls in tests

## 13. DEMO STORY

Numbers below are **illustrative placeholders**. Report whatever the engine actually produces.

1. Load demo merchant — 500 synthetic records across three sources
2. Run financial control — show throughput as it processes
3. Batch result: counts per state, coverage, confusion matrix, rupees at risk, plus the §9 honesty clause visible on screen
4. Open one VERIFIED transaction — truth graph, every edge green
5. Open one EXPLAINED settlement — the decomposer names fee drift and attributes exact rupees with proof
6. Open one HUMAN_REVIEW case — evidence list shows what passed and failed, unexplained amount stated plainly, no fabricated cause
7. Ask the copilot why the review queue totals what it does — answer cites specific record IDs

The strongest moment in the demo is step 5 into step 6: the system proving what it knows, then admitting what it doesn't.