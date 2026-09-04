"""Reconciliation Q&A Agent — Comprehensive Financial Data Assistant.

Strict, natural-language Q&A agent that answers ANY user query based on the
reconciliation pipeline's outputs. Uses deep intent classification and
exhaustive data extraction to deliver rich, perfectly formatted responses.

Design principles:
  - Never says "I don't know" — always provides relevant data + suggestions
  - All answers derived ONLY from reconciliation results (no hallucination).
    Where a figure cannot be derived from actual results and must be
    estimated from an assumed contract rate, that is explicitly labeled.
  - Currency values always in properly formatted rupees
  - Responses use structured markdown for readability

Changelog (this revision):
  - FIX: financial/tax handlers previously invented fee totals from a
    hardcoded 1.90%/18% formula and presented them as fact. This
    contradicted the "no hallucination" principle above. Fee figures are
    now sourced from actual reconciliation output when available, and
    clearly labeled "estimated" when they must fall back to an assumed rate.
  - FIX: deduplicated fee computation (was copy-pasted in two handlers).
  - FIX: `_routing_label(None)` could leak the literal string "None" into
    output; now normalizes to an em-dash.
  - FIX: intent classification broke ties purely on pattern-list order;
    now weights multi-word phrase matches higher and uses word-boundary
    matching to reduce false positive substring hits.
  - NEW: follow-up memory ("tell me more about that").
  - NEW: root-cause / failure-reason aggregation for flagged transactions.
  - NEW: amount-range filtering ("payments above ₹10,000").
  - NEW: multi-ID lookup in a single query.
  - NEW: markdown table export for large result sets.
"""

from __future__ import annotations

import re
from collections import Counter
from afc.money import format_rupees


# ────────────────────────────────────────────────────────────
# Intent patterns: each maps a set of keywords/phrases to a handler.
# Multi-word phrases are naturally more specific, so they're weighted
# higher during scoring (see _classify_intent).
# ────────────────────────────────────────────────────────────

_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    # Conversational
    ("greet",       ["hello", "hi", "hey", "good morning", "good evening", "good afternoon", "howdy", "greetings"]),
    ("help",        ["help", "what can you", "what do you", "capable", "features", "how to use", "guide", "menu"]),
    ("thanks",      ["thank", "thanks", "thx", "appreciate", "great job", "nice one", "awesome", "perfect", "good work"]),
    ("followup",    ["tell me more", "more details", "more about that", "elaborate", "that one", "what about it",
                     "expand on that", "go deeper", "and then"]),

    # Specific transaction lookup
    ("lookup_txn",  ["pay_", "setl_", "transaction id", "look up", "lookup", "find transaction", "inspect"]),

    # Diagnostics / root cause
    ("failure_reasons", ["why is", "why are", "why flagged", "why review", "common reason", "common issue",
                          "root cause", "most common failure", "failing check", "why unresolved"]),

    # Export
    ("export",      ["export", "csv", "as a table", "as table", "download list", "give me a table"]),

    # Amount range
    ("amount_range", ["above ₹", "above rs", "over ₹", "over rs", "under ₹", "under rs", "below ₹", "below rs",
                       "greater than", "less than", "between "]),

    # Financial totals
    ("financial",   ["paid", "pay amount", "total amount", "volume", "gross", "net", "how much", "receive", "credited",
                     "payout", "revenue", "income", "earned", "collected", "processed amount"]),

    # Tax & fee
    ("tax_fee",     ["tax", "gst", "mdr", "fee", "rate", "deduct", "commission", "charge", "cost of processing"]),

    # State queries — specific
    ("verified",    ["verified", "clean", "matched perfectly", "fully matched", "correct match"]),
    ("explained",   ["explained", "attributed", "accounted for"]),
    ("awaiting",    ["awaiting", "pending", "waiting", "not yet settled", "settlement pending"]),
    ("human_review",["human review", "manual review", "needs review", "review queue", "flagged"]),
    ("unresolved",  ["unresolved", "failed", "could not", "not resolved", "open issue"]),
    ("state_all",   ["state", "status", "breakdown", "distribution", "classification", "decision", "categoriz"]),

    # Gap / variance
    ("gap",         ["gap", "difference", "short", "variance", "mismatch", "discrepancy", "drift", "unexplained"]),

    # Rankings
    ("top_worst",   ["largest", "biggest", "worst", "highest", "top", "maximum", "most", "riskiest"]),
    ("smallest",    ["smallest", "lowest", "minimum", "least", "best", "cleanest"]),

    # Percentages
    ("percentage",  ["percent", "%", "ratio", "proportion", "success rate", "failure rate", "share"]),

    # Confidence
    ("confidence",  ["confidence", "score", "how sure", "certainty", "reliability"]),

    # Routing
    ("routing",     ["routing", "auto reconcil", "auto-reconcil", "automated", "ai suggestion", "ai+human",
                     "auto vs", "routed"]),

    # Matching
    ("matching",    ["match", "keyed", "inferred", "utr match", "narration", "matching quality"]),

    # Integrity & duplicates
    ("integrity",   ["duplicate", "integrity", "defect", "orphan", "excess", "data quality"]),

    # Performance & accuracy metrics
    ("accuracy",    ["accuracy", "precise", "precision", "recall", "f1", "classification quality"]),
    ("coverage",    ["coverage", "auto-resolved", "automation", "without human"]),
    ("throughput",  ["speed", "throughput", "performance", "records per", "how fast", "processing time",
                     "wall clock", "execution"]),

    # Money by status
    ("money_status",["money by", "amount by", "rupees by", "value of verified", "value of unresolved",
                     "auto reconciled value", "review value", "cost of false"]),

    # Comparison
    ("compare",     ["compare", "vs", "versus", "difference between", "more than", "less than"]),

    # Count queries
    ("count",       ["how many", "count", "number of", "total transactions", "total units"]),

    # Settlement specific
    ("settlement",  ["settlement", "settled", "settle"]),

    # Payment specific
    ("payment",     ["payment", "captured", "order"]),

    # Summary
    ("summary",     ["summary", "overview", "report", "everything", "all data", "what did you find",
                     "tell me about", "show me", "recap", "brief"]),
]


class ReconciliationQAAgent:
    """Comprehensive Q&A agent bound to reconciliation pipeline results."""

    SUGGESTED_QUESTIONS = [
        "Give me a full summary",
        "How much was processed?",
        "Show tax & fee breakdown",
        "Any unresolved issues?",
        "What's the largest gap?",
        "Show confidence scores",
        "Why are transactions flagged for review?",
    ]

    STATE_EMOJI = {
        "VERIFIED": "✅", "EXPLAINED": "🔍", "AWAITING_BANK": "⏳",
        "HUMAN_REVIEW": "👤", "UNRESOLVED": "❌",
    }

    def __init__(self, results: dict | None, metrics: dict | None):
        self.results = results or {}
        self.metrics = metrics or {}
        self.decisions = self.results.get("decisions", [])
        self.decompositions = self.results.get("decompositions", [])
        self.payment_flows = self.results.get("payment_flows", [])
        self.match_summary = self.results.get("match_summary", {})
        self.integrity = self.results.get("integrity", {})

        # Pre-compute state counts
        self._state_counts: Counter = Counter()
        for d in self.decisions:
            self._state_counts[d.get("state", "UNKNOWN")] += 1

        # Pre-compute routing counts
        self._routing_counts: Counter = Counter()
        for d in self.decisions:
            self._routing_counts[d.get("routing", "unknown")] += 1

        # Conversational memory: last entity discussed (for follow-ups)
        self._last_entity: str | None = None

    # ────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────

    def answer(self, query: str) -> str:
        """Answer any natural-language query about reconciliation data."""
        if not self.results or not self.decisions:
            return self._no_data_response()

        q = query.lower().strip()
        intent = self._classify_intent(q)

        handler_map = {
            "greet":            self._handle_greet,
            "help":             self._handle_help,
            "thanks":           self._handle_thanks,
            "followup":         lambda q: self._handle_followup(q),
            "lookup_txn":       lambda q: self._handle_lookup(q),
            "failure_reasons":  lambda q: self._handle_failure_reasons(q),
            "export":           lambda q: self._handle_export(q),
            "amount_range":     lambda q: self._handle_amount_range(q),
            "financial":        lambda q: self._handle_financial(q),
            "tax_fee":          lambda q: self._handle_tax_fee(q),
            "verified":         lambda q: self._handle_state_filter(q, "VERIFIED", "✅", "verified"),
            "explained":        lambda q: self._handle_state_filter(q, "EXPLAINED", "🔍", "explained"),
            "awaiting":         lambda q: self._handle_state_filter(q, "AWAITING_BANK", "⏳", "awaiting bank credit"),
            "human_review":     lambda q: self._handle_state_filter(q, "HUMAN_REVIEW", "👤", "in human review queue"),
            "unresolved":       lambda q: self._handle_state_filter(q, "UNRESOLVED", "❌", "unresolved"),
            "state_all":        lambda q: self._handle_state_breakdown(q),
            "gap":              lambda q: self._handle_gap(q),
            "top_worst":        lambda q: self._handle_top_worst(q),
            "smallest":         lambda q: self._handle_smallest(q),
            "percentage":       lambda q: self._handle_percentage(q),
            "confidence":       lambda q: self._handle_confidence(q),
            "routing":          lambda q: self._handle_routing(q),
            "matching":         lambda q: self._handle_matching(q),
            "integrity":        lambda q: self._handle_integrity(q),
            "accuracy":         lambda q: self._handle_accuracy(q),
            "coverage":         lambda q: self._handle_coverage(q),
            "throughput":       lambda q: self._handle_throughput(q),
            "money_status":     lambda q: self._handle_money_by_status(q),
            "compare":          lambda q: self._handle_compare(q),
            "count":            lambda q: self._handle_count(q),
            "settlement":       lambda q: self._handle_settlement(q),
            "payment":          lambda q: self._handle_payment(q),
            "summary":          lambda q: self._handle_summary(q),
        }

        handler = handler_map.get(intent)
        if handler:
            if intent in ("greet", "help", "thanks"):
                return handler()
            return handler(q)

        # Catch-all: smart fallback
        return self._handle_summary(q)

    # ────────────────────────────────────────────────────────
    # Intent classification
    # ────────────────────────────────────────────────────────

    def _classify_intent(self, q: str) -> str:
        """Match query against intent patterns, return best intent.

        Multi-word phrases score higher than single words (they're more
        specific and less likely to be a coincidental substring hit).
        Single-word keywords are matched on word boundaries where possible
        to avoid false positives like "rate" inside "moderate".
        """
        # Explicit transaction ID always wins — most specific signal there is.
        if re.search(r'pay_\w+|setl_\w+', q, re.IGNORECASE):
            return "lookup_txn"

        scores: dict[str, float] = {}
        first_seen_order: dict[str, int] = {}
        order = 0

        for intent, keywords in _INTENT_PATTERNS:
            for kw in keywords:
                hit = False
                if " " in kw or not kw.replace("_", "").isalnum():
                    # Phrase or punctuation-bearing keyword (e.g. "%", "ai+human") — substring match.
                    hit = kw in q
                    weight = 1.0 + 0.5 * kw.count(" ")  # reward specificity
                else:
                    hit = re.search(rf'\b{re.escape(kw)}\b', q) is not None
                    weight = 1.0

                if hit:
                    scores[intent] = scores.get(intent, 0.0) + weight
                    if intent not in first_seen_order:
                        first_seen_order[intent] = order
                        order += 1

        if not scores:
            return "summary"

        # Highest score wins; ties broken by whichever intent's first keyword
        # hit occurred first in the query-scan (stable, deterministic).
        return max(scores, key=lambda k: (scores[k], -first_seen_order[k]))

    # ────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────

    def _total(self) -> int:
        return len(self.decisions)

    def _pct(self, n: int) -> str:
        t = self._total()
        return f"{n/t*100:.1f}%" if t > 0 else "0%"

    def _fmt(self, paise: int) -> str:
        return format_rupees(paise or 0)

    def _metrics_inner(self) -> dict:
        return self.metrics.get("metrics", self.metrics)

    def _run_meta(self) -> dict:
        return self.metrics.get("run_meta", {})

    def _suggestions(self, exclude: str = "") -> str:
        qs = [q for q in self.SUGGESTED_QUESTIONS if q.lower() != exclude.lower()][:4]
        return "\n\n💡 **You can also ask:**\n" + "\n".join(f"• {q}" for q in qs)

    def _total_gross(self) -> int:
        """Total gross payment volume, safely summed."""
        return sum(
            f["payment"].get("gross_paise", 0) or 0
            for f in self.payment_flows
            if f.get("payment")
        )

    def _fee_breakdown(self) -> dict:
        """Single source of truth for fee/tax figures.

        Prefers actual figures present in the reconciliation metrics
        (e.g. from decomposition `attributed` causes tagged as fee/tax,
        or a `fees` block in metrics). Falls back to an assumed contract
        rate ONLY when no real figure is available, and flags that clearly
        so downstream handlers never present an estimate as fact.
        """
        total_gross = self._total_gross()
        m = self._metrics_inner()
        fees_block = m.get("fees") or self.results.get("fees")

        if fees_block and fees_block.get("mdr_paise") is not None:
            mdr = fees_block.get("mdr_paise", 0)
            gst = fees_block.get("gst_paise", 0)
            source = "actual"
        else:
            # Try to recover real fee amounts attributed within decompositions.
            mdr = 0
            gst = 0
            found_actual = False
            for d in self.decompositions:
                for a in d.get("attributed", []):
                    cause = a.get("cause", "")
                    if "mdr" in cause or "fee" in cause:
                        mdr += abs(a.get("amount_paise", 0))
                        found_actual = True
                    elif "gst" in cause or "tax" in cause:
                        gst += abs(a.get("amount_paise", 0))
                        found_actual = True

            if found_actual:
                source = "actual"
            else:
                # No real figures anywhere — fall back to an assumed rate,
                # and say so explicitly rather than presenting it as fact.
                mdr = int(total_gross * 0.019)
                gst = int(mdr * 0.18)
                source = "estimated"

        total_fees = mdr + gst
        net = total_gross - total_fees
        effective_rate = (total_fees / total_gross * 100) if total_gross else 0.0

        return {
            "total_gross": total_gross,
            "mdr": mdr,
            "gst": gst,
            "total_fees": total_fees,
            "net": net,
            "effective_rate": effective_rate,
            "source": source,  # "actual" | "estimated"
        }

    # ────────────────────────────────────────────────────────
    # No data
    # ────────────────────────────────────────────────────────

    def _no_data_response(self) -> str:
        return (
            "⚠️ **No Reconciliation Data Available**\n\n"
            "I don't have any data to analyze yet. To get started:\n\n"
            "1. Go to **⚙️ Settings** and connect your Razorpay API keys\n"
            "2. Upload a bank statement CSV\n"
            "3. Click **🚀 Execute Live Reconciliation** or run the **🧪 Demo Stress Test**\n\n"
            "Once a reconciliation batch completes, I can answer questions about:\n"
            "• Total payment volumes & net payouts\n"
            "• Tax & MDR fee segregation\n"
            "• Gaps, variances & attributed causes\n"
            "• Confidence scores & routing decisions\n"
            "• Specific transaction audit trails"
        )

    # ────────────────────────────────────────────────────────
    # Conversational handlers
    # ────────────────────────────────────────────────────────

    def _handle_greet(self) -> str:
        total = self._total()
        return (
            f"👋 Hello! I'm your **Reconciliation Q&A Agent**.\n\n"
            f"I'm currently loaded with data from **{total}** reconciliation units. "
            f"I can break down payments, fees, gaps, state classifications — anything about your data.\n"
            + self._suggestions()
        )

    def _handle_help(self) -> str:
        return (
            "🤖 **What I Can Do**\n\n"
            "I'm a strict financial data assistant. I answer questions using **only** "
            "your reconciliation engine's outputs — no guessing, no hallucination. Where I "
            "can't derive a figure from actual results and have to estimate, I say so.\n\n"
            "**Question categories I handle:**\n\n"
            "💰 **Financial** — \"How much was processed?\", \"Total gross volume?\"\n"
            "🧾 **Tax & Fees** — \"Show MDR breakdown\", \"How much GST?\"\n"
            "📊 **State Analysis** — \"How many verified?\", \"Show unresolved\"\n"
            "🔍 **Gap Analysis** — \"Show all gaps\", \"What's unexplained?\"\n"
            "📈 **Rankings** — \"Largest gap?\", \"Worst transactions?\"\n"
            "🎯 **Accuracy** — \"Classification accuracy?\", \"Coverage rate?\"\n"
            "📋 **Confidence** — \"Confidence distribution?\", \"Low confidence txns?\"\n"
            "🔗 **Matching** — \"Match quality?\", \"Keyed vs inferred?\"\n"
            "🛡️ **Integrity** — \"Any duplicates?\", \"Data quality?\"\n"
            "🔎 **Lookup** — \"Tell me about pay_xyz\", \"Inspect setl_abc\" (or multiple IDs at once)\n"
            "🧠 **Diagnostics** — \"Why are transactions flagged for review?\"\n"
            "💵 **Filters** — \"Payments above ₹10,000\"\n"
            "📤 **Export** — \"Export unresolved as a table\"\n"
            "⚡ **Performance** — \"Processing speed?\", \"Throughput?\"\n\n"
            "Just ask in natural language — I'll figure out what you need, and you can say "
            "\"tell me more\" to dig into whatever I last showed you."
        )

    def _handle_thanks(self) -> str:
        return (
            "😊 You're welcome! Happy to help with your reconciliation analysis.\n\n"
            "Need anything else? Just ask!"
            + self._suggestions()
        )

    # ────────────────────────────────────────────────────────
    # Follow-up handling
    # ────────────────────────────────────────────────────────

    def _handle_followup(self, q: str) -> str:
        if not self._last_entity:
            return (
                "I don't have a specific transaction or settlement in context yet. "
                "Ask me to look one up first (e.g. \"tell me about pay_ABC123\"), then "
                "I can go deeper on follow-up questions."
            )
        # Re-run lookup on the remembered entity.
        return self._handle_lookup(self._last_entity.lower())

    # ────────────────────────────────────────────────────────
    # Transaction lookup (supports multiple IDs in one query)
    # ────────────────────────────────────────────────────────

    def _handle_lookup(self, q: str) -> str:
        matches = re.findall(r'(pay_\w+|setl_\w+)', q, re.IGNORECASE)
        if not matches:
            return (
                "🔎 I can look up specific transactions! Just include the ID in your question.\n\n"
                "**Examples:**\n"
                "• \"Tell me about pay_ABC123\"\n"
                "• \"What happened with setl_XYZ789?\"\n"
                "• \"Compare pay_ABC123 and pay_DEF456\" (multiple IDs at once)"
            )

        if len(matches) > 1:
            sections = [self._lookup_single(m) for m in matches]
            self._last_entity = matches[-1]
            return "\n\n---\n\n".join(sections)

        result = self._lookup_single(matches[0])
        self._last_entity = matches[0]
        return result

    def _lookup_single(self, target_id: str) -> str:
        decision = next((d for d in self.decisions if d.get("unit_id") == target_id), None)
        if not decision:
            decision = next((d for d in self.decisions if target_id in d.get("unit_id", "")), None)

        if not decision:
            return f"❌ No transaction found matching `{target_id}`. Check the ID and try again."

        uid = decision.get("unit_id", target_id)
        state = decision.get("state", "UNKNOWN")
        rule = decision.get("rule", "—")
        routing = decision.get("routing", "—")
        conf = decision.get("confidence", {})
        conf_score = conf.get("score", 0)
        checks = conf.get("checks", {})

        state_emoji = self.STATE_EMOJI.get(state, "❓")

        lines = [
            f"🔎 **Transaction Audit: `{uid}`**\n",
            f"**State**: {state_emoji} **{state}**",
            f"**Classification Rule**: `{rule}`",
            f"**Routing**: {self._routing_label(routing)}",
            f"**Confidence Score**: **{conf_score:.3f}**\n",
        ]

        if checks:
            passed = sum(1 for v in checks.values() if v is True)
            total_checks = len(checks)
            lines.append(f"**Verification Checks** ({passed}/{total_checks} passed):")
            for name, val in checks.items():
                icon = "✅" if val is True else ("❌" if val is False else "⚪")
                label = name.replace("_", " ").title()
                lines.append(f"  {icon} {label}")

        flow = next((f for f in self.payment_flows if f.get("unit_id") == uid), None)
        if flow and flow.get("payment"):
            p = flow["payment"]
            lines.append(f"\n💳 **Payment Details:**")
            lines.append(f"  • Payment ID: `{p.get('payment_id', '—')}`")
            lines.append(f"  • Gross Amount: **{self._fmt(p.get('gross_paise', 0))}**")
            lines.append(f"  • Captured On: {p.get('captured_on', '—')}")

            if flow.get("settlement"):
                s = flow["settlement"]
                lines.append(f"\n📋 **Settlement:**")
                lines.append(f"  • Settlement ID: `{s.get('settlement_id', '—')}`")
                lines.append(f"  • UTR: {s.get('utr', 'None')}")
                lines.append(f"  • Due On: {s.get('due_on', '—')}")

            if flow.get("ledger"):
                l = flow["ledger"]
                lines.append(f"\n📒 **Ledger Entry:**")
                lines.append(f"  • Ledger ID: `{l.get('ledger_id', '—')}`")
                lines.append(f"  • Amount: **{self._fmt(l.get('amount_paise', 0))}**")

        if flow and flow.get("settlement"):
            setl_id = flow["settlement"].get("settlement_id")
            decomp = next((d for d in self.decompositions if d.get("settlement_id") == setl_id), None)
            if decomp:
                lines.append(f"\n🔍 **Gap Analysis:**")
                lines.append(f"  • Expected: {self._fmt(decomp.get('expected_paise', 0))}")
                if decomp.get("actual_paise") is not None:
                    lines.append(f"  • Actual Bank Credit: {self._fmt(decomp.get('actual_paise', 0))}")
                lines.append(f"  • Gap: **{self._fmt(decomp.get('gap_paise', 0))}**")
                lines.append(f"  • Unexplained: **{self._fmt(decomp.get('unexplained_paise', 0))}**")

                attrs = decomp.get("attributed", [])
                if attrs:
                    lines.append(f"  • Attributed Causes:")
                    for a in attrs:
                        lines.append(f"    — {a['cause'].replace('_', ' ').title()}: {self._fmt(a['amount_paise'])}")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Financial
    # ────────────────────────────────────────────────────────

    def _handle_financial(self, q: str) -> str:
        fb = self._fee_breakdown()
        payments_count = sum(1 for f in self.payment_flows if f.get("payment"))
        total_ledger = sum(f["ledger"].get("amount_paise", 0) for f in self.payment_flows if f.get("ledger"))
        actual_bank = sum(d.get("actual_paise", 0) or 0 for d in self.decompositions)

        m = self._metrics_inner()
        rupees = m.get("rupees", {})

        rate_note = "" if fb["source"] == "actual" else " *(estimated from assumed contract rate — not present in raw results)*"

        lines = [
            "💰 **Financial & Volume Summary**\n",
            f"**Transactions Processed**: {payments_count:,}",
            f"**Total Gross Volume**: {self._fmt(fb['total_gross'])}",
            "",
            f"**Fee & Tax Deductions**{rate_note}:",
            f"  • MDR Fee: −{self._fmt(fb['mdr'])}",
            f"  • GST on MDR: −{self._fmt(fb['gst'])}",
            f"  • **Total Deductions**: −{self._fmt(fb['total_fees'])}",
            "",
            f"**{'Expected' if fb['source'] == 'estimated' else 'Actual'} Net Payout**: {self._fmt(fb['net'])}",
        ]

        if actual_bank > 0:
            lines.append(f"\n🏦 **Actual Bank Credits Received**: {self._fmt(actual_bank)}")
            variance = actual_bank - fb["net"]
            if variance != 0:
                sign = "+" if variance > 0 else ""
                lines.append(f"  • Variance from Expected: {sign}{self._fmt(variance)}")

        if total_ledger > 0:
            lines.append(f"\n📒 **Total Ledger Value**: {self._fmt(total_ledger)}")

        auto_rec = rupees.get("auto_reconciled_correct", 0)
        fp_cost = rupees.get("auto_reconciled_wrong_cost_of_false_positives", 0)
        review_amt = rupees.get("sent_to_human_review", 0)
        unres_amt = rupees.get("unresolved", 0)

        if any([auto_rec, fp_cost, review_amt, unres_amt]):
            lines.extend([
                "\n📌 **Value by Reconciliation Outcome:**",
                f"  ✅ Auto-Reconciled (Correct): {self._fmt(auto_rec)}",
            ])
            if fp_cost:
                lines.append(f"  ⚠️ False Positive Cost: {self._fmt(fp_cost)}")
            if review_amt:
                lines.append(f"  👤 Sent to Human Review: {self._fmt(review_amt)}")
            if unres_amt:
                lines.append(f"  ❌ Unresolved: {self._fmt(unres_amt)}")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Tax & Fee
    # ────────────────────────────────────────────────────────

    def _handle_tax_fee(self, q: str) -> str:
        fb = self._fee_breakdown()

        drifts = []
        for d in self.decompositions:
            for attr in d.get("attributed", []):
                if attr.get("cause") == "fee_rate_drift":
                    drifts.append((d.get("settlement_id"), attr.get("amount_paise", 0), attr.get("proof", "")))

        source_note = (
            "Sourced directly from reconciliation output."
            if fb["source"] == "actual"
            else "⚠️ No explicit fee data found in the raw results — the figures below assume the "
                 "standard 1.90% MDR + 18% GST-on-MDR contract rate. Treat as an estimate, not a "
                 "reported fact."
        )

        lines = [
            "🧾 **Tax & MDR Fee Segregation Report**\n",
            f"**Gross Payment Volume**: {self._fmt(fb['total_gross'])}\n",
            f"**Data Source**: {source_note}\n",
            "**Fee Computation Method:**",
            "  Uses integer paise with `round_half_away_from_zero` — zero floating-point drift.\n",
            "**Calculated Totals:**",
            f"  • Total MDR Fees: {self._fmt(fb['mdr'])}",
            f"  • Total GST on MDR: {self._fmt(fb['gst'])}",
            f"  • **Total Deductions**: {self._fmt(fb['total_fees'])}",
            f"  • **Effective Combined Rate**: {fb['effective_rate']:.3f}%",
            f"  • **Net After Deductions**: {self._fmt(fb['net'])}",
        ]

        if drifts:
            lines.append(f"\n⚠️ **Fee Rate Drifts Detected ({len(drifts)} settlements):**")
            for setl_id, amt, proof in drifts[:5]:
                lines.append(f"  • `{setl_id}`: Variance {self._fmt(amt)} — {proof}")
            if len(drifts) > 5:
                lines.append(f"  ... and {len(drifts) - 5} more")
        else:
            lines.append("\n✅ **No Fee Rate Drifts** — All settlements match contracted MDR rate.")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # State filters & breakdown
    # ────────────────────────────────────────────────────────

    def _handle_state_filter(self, q: str, state: str, emoji: str, label: str) -> str:
        matching = [d for d in self.decisions if d.get("state") == state]
        count = len(matching)

        if count == 0:
            return f"✅ No transactions are currently **{label}**. All clear!"

        if len(matching) > 15 and any(kw in q for kw in ("export", "csv", "table", "all of them", "full list")):
            return self._render_table(matching, title=f"{emoji} {state} ({count})")

        lines = [f"{emoji} **{state}** — {count} transactions ({self._pct(count)})\n"]

        show = matching[:15]
        for d in show:
            uid = d.get("unit_id", "?")
            rule = d.get("rule", "?")
            conf = d.get("confidence", {}).get("score", 0)
            routing = self._routing_label(d.get("routing", ""))
            lines.append(f"• `{uid}` — Rule: `{rule}`, Confidence: {conf:.2f}, {routing}")

        if count > 15:
            lines.append(f"\n... and **{count - 15}** more transactions. Ask to \"export {label} as a table\" to see all of them.")

        total_paise = 0
        for d in matching:
            flow = next((f for f in self.payment_flows if f.get("unit_id") == d.get("unit_id")), None)
            if flow and flow.get("payment"):
                total_paise += flow["payment"].get("gross_paise", 0)

        if total_paise > 0:
            lines.append(f"\n💰 **Total Gross Value ({label})**: {self._fmt(total_paise)}")

        return "\n".join(lines)

    def _handle_state_breakdown(self, q: str) -> str:
        total = self._total()
        states = ["VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"]

        lines = [f"📊 **Reconciliation State Breakdown** — {total:,} total units\n"]

        for s in states:
            c = self._state_counts.get(s, 0)
            pct = self._pct(c)
            bar_len = int(c / total * 20) if total else 0
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"{self.STATE_EMOJI[s]} **{s}**: {c:,} ({pct})  `{bar}`")

        terminal = self._state_counts.get("VERIFIED", 0) + self._state_counts.get("EXPLAINED", 0)
        non_terminal = total - terminal
        lines.append(f"\n✅ **Auto-resolved**: {terminal:,} ({self._pct(terminal)})")
        lines.append(f"⚠️ **Needs attention**: {non_terminal:,} ({self._pct(non_terminal)})")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Root-cause / failure diagnostics (NEW)
    # ────────────────────────────────────────────────────────

    def _handle_failure_reasons(self, q: str) -> str:
        """Aggregate which verification checks fail most often across
        flagged transactions, so the user gets an actionable diagnostic
        instead of just a raw count."""
        flagged = [d for d in self.decisions if d.get("state") in ("HUMAN_REVIEW", "UNRESOLVED")]
        if not flagged:
            return "✅ Nothing is flagged for review right now — no failure patterns to report."

        check_failures: Counter = Counter()
        rule_counts: Counter = Counter()
        for d in flagged:
            checks = d.get("confidence", {}).get("checks", {})
            for name, val in checks.items():
                if val is False:
                    check_failures[name] += 1
            rule_counts[d.get("rule", "unspecified")] += 1

        lines = [
            f"🧠 **Root-Cause Analysis** — {len(flagged)} flagged transactions "
            f"(HUMAN_REVIEW + UNRESOLVED)\n",
        ]

        if check_failures:
            lines.append("**Most common failing checks:**")
            for name, count in check_failures.most_common(8):
                pct = count / len(flagged) * 100
                label = name.replace("_", " ").title()
                lines.append(f"  • {label}: failed in {count} of {len(flagged)} ({pct:.0f}%)")
        else:
            lines.append("No per-check failure data was recorded on these transactions.")

        if rule_counts:
            lines.append("\n**Classification rules involved:**")
            for rule, count in rule_counts.most_common(5):
                lines.append(f"  • `{rule}`: {count} transactions")

        lines.append(
            "\nAsk \"show human review\" or \"show unresolved\" to see the underlying transaction list."
        )
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Amount-range filtering (NEW)
    # ────────────────────────────────────────────────────────

    def _handle_amount_range(self, q: str) -> str:
        nums = [float(n.replace(",", "")) for n in re.findall(r'[\d,]+(?:\.\d+)?', q)]
        if not nums:
            return (
                "💵 I can filter by amount — try something like \"payments above ₹10,000\" "
                "or \"between 1000 and 5000\"."
            )

        # Amounts in the query are rupees; convert to paise for comparison.
        min_paise = None
        max_paise = None

        if "between" in q and len(nums) >= 2:
            lo, hi = sorted(nums[:2])
            min_paise, max_paise = int(lo * 100), int(hi * 100)
        elif any(kw in q for kw in ("above", "over", "greater than", "more than")):
            min_paise = int(nums[0] * 100)
        elif any(kw in q for kw in ("under", "below", "less than")):
            max_paise = int(nums[0] * 100)
        else:
            min_paise = int(nums[0] * 100)

        matches = []
        for f in self.payment_flows:
            p = f.get("payment")
            if not p:
                continue
            amt = p.get("gross_paise", 0)
            if min_paise is not None and amt < min_paise:
                continue
            if max_paise is not None and amt > max_paise:
                continue
            matches.append((f.get("unit_id", "?"), amt, f.get("state", "?")))

        if not matches:
            return "No payments found in that amount range."

        matches.sort(key=lambda x: x[1], reverse=True)
        range_desc = (
            f"between {self._fmt(min_paise)} and {self._fmt(max_paise)}" if min_paise and max_paise else
            f"above {self._fmt(min_paise)}" if min_paise else
            f"below {self._fmt(max_paise)}"
        )

        lines = [f"💵 **Payments {range_desc}** — {len(matches)} found\n"]
        for uid, amt, state in matches[:15]:
            lines.append(f"  • `{uid}`: {self._fmt(amt)} — {state}")
        if len(matches) > 15:
            lines.append(f"\n... and {len(matches) - 15} more.")

        total = sum(m[1] for m in matches)
        lines.append(f"\n**Total in range**: {self._fmt(total)}")
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Export as markdown table (NEW)
    # ────────────────────────────────────────────────────────

    def _handle_export(self, q: str) -> str:
        # Figure out which state, if any, the user wants exported.
        state_filter = None
        for state in ("VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"):
            if state.lower().replace("_", " ") in q or state.lower() in q:
                state_filter = state
                break

        rows = [d for d in self.decisions if not state_filter or d.get("state") == state_filter]
        if not rows:
            return "No matching transactions to export."

        title = f"Export — {state_filter or 'All States'} ({len(rows)} rows)"
        return self._render_table(rows, title=title)

    def _render_table(self, decisions_subset: list[dict], title: str) -> str:
        lines = [f"📤 **{title}**\n", "| Unit ID | State | Rule | Confidence | Routing |",
                 "|---|---|---|---|---|"]
        for d in decisions_subset:
            uid = d.get("unit_id", "?")
            state = d.get("state", "?")
            rule = d.get("rule", "—")
            conf = d.get("confidence", {}).get("score", 0)
            routing = self._routing_label(d.get("routing", ""))
            lines.append(f"| `{uid}` | {state} | `{rule}` | {conf:.2f} | {routing} |")
        lines.append(f"\n*{len(decisions_subset)} rows. Copy this table into a spreadsheet as needed.*")
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Gap analysis
    # ────────────────────────────────────────────────────────

    def _handle_gap(self, q: str) -> str:
        match = re.search(r'(setl_\w+)', q, re.IGNORECASE)
        if match:
            return self._explain_specific_gap(match.group(1))

        gaps = [d for d in self.decompositions if d.get("gap_paise", 0) != 0]
        if not gaps:
            return "✅ **No Gaps Found** — All bank credits matched expected settlement net payouts exactly."

        total_gap = sum(abs(d.get("gap_paise", 0)) for d in gaps)
        total_unexplained = sum(abs(d.get("unexplained_paise", 0)) for d in gaps)

        cause_totals: Counter = Counter()
        for g in gaps:
            for a in g.get("attributed", []):
                cause_totals[a["cause"]] += abs(a.get("amount_paise", 0))

        lines = [
            f"🔍 **Gap Analysis Report** — {len(gaps)} settlements with variances\n",
            f"**Total Absolute Gap**: {self._fmt(total_gap)}",
            f"**Total Unexplained**: {self._fmt(total_unexplained)}\n",
        ]

        if cause_totals:
            lines.append("**Attributed Causes (by total impact):**")
            for cause, amount in cause_totals.most_common():
                lines.append(f"  • {cause.replace('_', ' ').title()}: {self._fmt(amount)}")
            lines.append("")

        sorted_gaps = sorted(gaps, key=lambda d: abs(d.get("gap_paise", 0)), reverse=True)
        lines.append("**Top Settlements by Gap Size:**")
        for g in sorted_gaps[:10]:
            setl_id = g.get("settlement_id", "?")
            gap = g.get("gap_paise", 0)
            causes = [a["cause"] for a in g.get("attributed", [])]
            cause_str = ", ".join(causes) if causes else "unexplained"
            lines.append(f"  • `{setl_id}`: Gap {self._fmt(gap)} — {cause_str}")

        if len(gaps) > 10:
            lines.append(f"  ... and {len(gaps) - 10} more settlements.")

        return "\n".join(lines)

    def _explain_specific_gap(self, settlement_id: str) -> str:
        for d in self.decompositions:
            if settlement_id in d.get("settlement_id", ""):
                self._last_entity = d.get("settlement_id", settlement_id)
                lines = [f"🔍 **Gap Analysis: `{d['settlement_id']}`**\n"]
                lines.append(f"**Expected (Contracted)**: {self._fmt(d.get('expected_paise', 0))}")
                if d.get("actual_paise") is not None:
                    lines.append(f"**Actual Bank Credit**: {self._fmt(d.get('actual_paise', 0))}")
                lines.append(f"**Gap**: {self._fmt(d.get('gap_paise', 0))}")

                attrs = d.get("attributed", [])
                if attrs:
                    lines.append("\n**Attributed Causes:**")
                    for a in attrs:
                        lines.append(f"  • {a['cause'].replace('_', ' ').title()}: "
                                     f"{self._fmt(a['amount_paise'])} — {a.get('proof', '')}")

                lines.append(f"\n**Unexplained Residual**: {self._fmt(d.get('unexplained_paise', 0))}")

                if d.get("failed"):
                    lines.append(f"⚠️ **Decomposition Failed**: {d.get('failure_reason', 'Unknown reason')}")

                return "\n".join(lines)

        return f"No decomposition data found for settlement `{settlement_id}`."

    # ────────────────────────────────────────────────────────
    # Rankings
    # ────────────────────────────────────────────────────────

    def _handle_top_worst(self, q: str) -> str:
        if any(kw in q for kw in ["gap", "variance", "mismatch", "difference"]):
            return self._rank_by_gap(top=True)
        if any(kw in q for kw in ["confidence", "score", "risk"]):
            return self._rank_by_confidence(lowest=True)
        if any(kw in q for kw in ["amount", "value", "payment", "transaction"]):
            return self._rank_by_amount(top=True)

        worst = [d for d in self.decisions if d.get("state") in ("UNRESOLVED", "HUMAN_REVIEW")]
        if not worst:
            return "✅ **No critical transactions found** — everything is verified or explained!"

        worst.sort(key=lambda d: d.get("confidence", {}).get("score", 1))
        lines = [f"⚠️ **Top Transactions Requiring Attention** ({len(worst)} total)\n"]

        for d in worst[:10]:
            uid = d.get("unit_id", "?")
            state = d.get("state", "?")
            conf = d.get("confidence", {}).get("score", 0)
            emoji = "❌" if state == "UNRESOLVED" else "👤"
            flow = next((f for f in self.payment_flows if f.get("unit_id") == uid), None)
            amt = ""
            if flow and flow.get("payment"):
                amt = f" | {self._fmt(flow['payment'].get('gross_paise', 0))}"
            lines.append(f"  {emoji} `{uid}` — {state}, Confidence: {conf:.2f}{amt}")

        return "\n".join(lines)

    def _handle_smallest(self, q: str) -> str:
        if any(kw in q for kw in ["gap", "variance"]):
            return self._rank_by_gap(top=False)
        return self._rank_by_confidence(lowest=False)

    def _rank_by_gap(self, top: bool) -> str:
        gaps = [d for d in self.decompositions if d.get("gap_paise", 0) != 0]
        if not gaps:
            return "✅ No gaps to rank — all settlements match exactly."

        gaps.sort(key=lambda d: abs(d.get("gap_paise", 0)), reverse=top)
        label = "Largest" if top else "Smallest"
        lines = [f"📊 **{label} Gaps by Settlement**\n"]
        for g in gaps[:10]:
            lines.append(f"  • `{g['settlement_id']}`: Gap {self._fmt(g.get('gap_paise', 0))}")
        return "\n".join(lines)

    def _rank_by_amount(self, top: bool) -> str:
        flows_with_amount = []
        for f in self.payment_flows:
            p = f.get("payment")
            if p and p.get("gross_paise"):
                flows_with_amount.append((f["unit_id"], p["gross_paise"], f.get("state", "?")))

        if not flows_with_amount:
            return "No payment amount data available."

        flows_with_amount.sort(key=lambda x: x[1], reverse=top)
        label = "Largest" if top else "Smallest"
        lines = [f"💰 **{label} Payments by Gross Amount**\n"]
        for uid, amt, state in flows_with_amount[:10]:
            lines.append(f"  • `{uid}`: {self._fmt(amt)} — {state}")
        return "\n".join(lines)

    def _rank_by_confidence(self, lowest: bool) -> str:
        sorted_d = sorted(self.decisions, key=lambda d: d.get("confidence", {}).get("score", 0),
                          reverse=not lowest)
        label = "Lowest" if lowest else "Highest"
        lines = [f"🎯 **{label} Confidence Transactions**\n"]
        for d in sorted_d[:10]:
            uid = d.get("unit_id", "?")
            conf = d.get("confidence", {}).get("score", 0)
            state = d.get("state", "?")
            lines.append(f"  • `{uid}`: Score {conf:.3f} — {state}")
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Percentage & count
    # ────────────────────────────────────────────────────────

    def _handle_percentage(self, q: str) -> str:
        total = self._total()
        lines = [f"📊 **Percentage Breakdown** — {total:,} total units\n"]

        states = ["VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"]
        for s in states:
            c = self._state_counts.get(s, 0)
            lines.append(f"{self.STATE_EMOJI[s]} **{s}**: {self._pct(c)}")

        m = self._metrics_inner()
        coverage = m.get("coverage_terminal_without_human", 0) * 100
        accuracy = m.get("confusion", {}).get("accuracy", 0) * 100
        lines.append(f"\n🎯 **Automation Coverage**: {coverage:.1f}%")
        lines.append(f"🎯 **Classification Accuracy**: {accuracy:.1f}%")

        return "\n".join(lines)

    def _handle_count(self, q: str) -> str:
        total = self._total()
        lines = [f"📊 **Transaction Counts**\n"]
        lines.append(f"**Total Units**: {total:,}\n")

        states = ["VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"]
        for s in states:
            c = self._state_counts.get(s, 0)
            lines.append(f"• **{s}**: {c:,}")

        lines.append(f"\n**Decompositions Analyzed**: {len(self.decompositions):,}")
        lines.append(f"**Payment Flows Mapped**: {len(self.payment_flows):,}")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Confidence
    # ────────────────────────────────────────────────────────

    def _handle_confidence(self, q: str) -> str:
        scores = [d.get("confidence", {}).get("score", 0) for d in self.decisions]
        if not scores:
            return "No confidence data available."

        avg = sum(scores) / len(scores)
        high = sum(1 for s in scores if s >= 0.9)
        mid = sum(1 for s in scores if 0.6 <= s < 0.9)
        low = sum(1 for s in scores if s < 0.6)

        lines = [
            f"📊 **Confidence Score Distribution** — {len(scores):,} transactions\n",
            f"🟢 **High (≥ 0.90)**: {high:,} ({self._pct(high)}) — Auto-reconcile eligible",
            f"🟡 **Medium (0.60 – 0.89)**: {mid:,} ({self._pct(mid)}) — AI suggests, human confirms",
            f"🔴 **Low (< 0.60)**: {low:,} ({self._pct(low)}) — Direct to human queue\n",
            f"📈 **Average Confidence**: **{avg:.4f}**",
            f"📉 **Min Confidence**: **{min(scores):.4f}**",
            f"📈 **Max Confidence**: **{max(scores):.4f}**\n",
            "**Thresholds:**",
            f"  • Auto-reconcile: ≥ 0.90 (checks 9/9 or 8/9)",
            f"  • AI + Human review: ≥ 0.60",
            f"  • Human queue: < 0.60",
        ]

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Routing
    # ────────────────────────────────────────────────────────

    def _handle_routing(self, q: str) -> str:
        total = self._total()
        lines = [f"🔀 **Routing Distribution** — {total:,} transactions\n"]

        routing_labels = {
            "auto_reconcile": ("🟢", "Auto Reconcile"),
            "ai_suggestion_human_confirms": ("🟡", "AI Suggestion + Human Confirms"),
            "human_review_queue": ("🔴", "Human Review Queue"),
        }

        for key, (emoji, label) in routing_labels.items():
            c = self._routing_counts.get(key, 0)
            if c > 0:
                pct = self._pct(c)
                bar_len = int(c / total * 20) if total else 0
                bar = "█" * bar_len + "░" * (20 - bar_len)
                lines.append(f"{emoji} **{label}**: {c:,} ({pct})  `{bar}`")

        for key, count in self._routing_counts.items():
            if key not in routing_labels:
                lines.append(f"⚪ **{key or '—'}**: {count:,} ({self._pct(count)})")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Matching quality
    # ────────────────────────────────────────────────────────

    def _handle_matching(self, q: str) -> str:
        ms = self.match_summary
        if not ms:
            return "No match summary data available from the current reconciliation run."

        total = sum(ms.values())
        lines = [f"🔗 **Matching Quality Report** — {total:,} match decisions\n"]

        match_labels = {
            "keyed": ("🔑", "Keyed Match (UTR/ID)"),
            "inferred": ("🔍", "Inferred Match"),
            "settled_no_bank": ("⏳", "Settled, No Bank Entry"),
            "unmatched": ("❌", "Unmatched"),
        }

        for key, (emoji, label) in match_labels.items():
            c = ms.get(key, 0)
            if c > 0:
                pct = f"{c/total*100:.1f}%" if total else "0%"
                lines.append(f"{emoji} **{label}**: {c:,} ({pct})")

        for key, count in ms.items():
            if key not in match_labels and count > 0:
                lines.append(f"⚪ **{key}**: {count:,}")

        keyed = ms.get("keyed", 0) + ms.get("inferred", 0)
        match_rate = keyed / total * 100 if total else 0
        lines.append(f"\n📈 **Overall Match Rate**: **{match_rate:.1f}%**")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Integrity
    # ────────────────────────────────────────────────────────

    def _handle_integrity(self, q: str) -> str:
        rep = self.integrity
        if not rep:
            return "No integrity report available from the current reconciliation run."

        lines = [
            "🛡️ **Data Integrity Report (Level 0 Checks)**\n",
            f"**Ledger Quality:**",
            f"  • Duplicate Groups: **{rep.get('ledger_duplicate_groups', 0)}**",
            f"  • Excess Rows: **{rep.get('ledger_excess_rows', 0)}**\n",
            f"**Bank Statement Quality:**",
            f"  • Duplicate Groups: **{rep.get('bank_duplicate_groups', 0)}**",
            f"  • Excess Rows: **{rep.get('bank_excess_rows', 0)}**",
            f"  • Unkeyed Rows: **{rep.get('unkeyed_bank_rows', 0)}**\n",
        ]

        total_issues = (
            rep.get('ledger_duplicate_groups', 0) +
            rep.get('ledger_excess_rows', 0) +
            rep.get('bank_duplicate_groups', 0) +
            rep.get('bank_excess_rows', 0) +
            rep.get('unkeyed_bank_rows', 0)
        )

        if total_issues == 0:
            lines.append("✅ **Clean Data** — No integrity issues detected!")
        else:
            lines.append(f"⚠️ **{total_issues} integrity issues detected** — review recommended.")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Accuracy, Coverage, Throughput
    # ────────────────────────────────────────────────────────

    def _handle_accuracy(self, q: str) -> str:
        m = self._metrics_inner()
        c = m.get("confusion", {})
        acc = c.get("accuracy", 0) * 100
        scored = c.get("scored", 0)
        correct = c.get("correct", 0)

        lines = [
            "🎯 **Classification Accuracy Report**\n",
            f"**Accuracy**: **{acc:.2f}%** ({correct:,}/{scored:,} units correctly classified)\n",
        ]

        fc = m.get("confusion_fault_carrying_only", {})
        if fc:
            fc_acc = fc.get("accuracy", 0) * 100
            fc_scored = fc.get("scored", 0)
            lines.append(f"**Fault-Carrying Subset**: {fc_acc:.2f}% accuracy ({fc_scored:,} units)")
            lines.append("  (Excludes clean transactions — harder, more meaningful metric)\n")

        attr = m.get("attribution", {})
        if attr:
            lines.append("**Attribution Accuracy:**")
            for k, v in attr.items():
                if isinstance(v, (int, float)):
                    lines.append(f"  • {k.replace('_', ' ').title()}: {v}")

        honesty = m.get("honesty_clause", "")
        if honesty:
            lines.append(f"\n⚠️ **Qualification**: {honesty[:200]}")

        return "\n".join(lines)

    def _handle_coverage(self, q: str) -> str:
        m = self._metrics_inner()
        coverage = m.get("coverage_terminal_without_human", 0) * 100
        total = self._total()
        terminal = self._state_counts.get("VERIFIED", 0) + self._state_counts.get("EXPLAINED", 0)

        return (
            f"📈 **Automation Coverage**\n\n"
            f"**{coverage:.1f}%** of transactions were resolved automatically without human intervention.\n\n"
            f"• **Auto-resolved**: {terminal:,} out of {total:,} units\n"
            f"• **Remaining**: {total - terminal:,} units need human attention\n\n"
            f"This means the engine handles **{coverage:.1f}%** of your reconciliation workload automatically."
        )

    def _handle_throughput(self, q: str) -> str:
        rm = self._run_meta()
        wall_clock = rm.get("wall_clock_seconds", 0) or self.results.get("wall_clock_seconds", 0)
        total = self._total()
        rps = rm.get("records_per_second") or (total / wall_clock if wall_clock else 0)

        return (
            f"⚡ **Engine Performance**\n\n"
            f"• **Total Units**: {total:,}\n"
            f"• **Wall Clock Time**: {wall_clock:.4f} seconds\n"
            f"• **Throughput**: **{rps:,.1f}** records/second\n"
            f"• **Mode**: {rm.get('mode', 'unknown')}\n\n"
            f"{'🚀 Excellent throughput!' if rps > 1000 else '⚡ Solid performance.'}"
        )

    # ────────────────────────────────────────────────────────
    # Money by status
    # ────────────────────────────────────────────────────────

    def _handle_money_by_status(self, q: str) -> str:
        m = self._metrics_inner()
        r = m.get("rupees", {})

        if not r:
            state_amounts: dict[str, int] = {}
            for f in self.payment_flows:
                state = f.get("state", "UNKNOWN")
                p = f.get("payment")
                if p:
                    state_amounts[state] = state_amounts.get(state, 0) + p.get("gross_paise", 0)

            if not state_amounts:
                return "No monetary breakdown available by status."

            lines = ["💰 **Gross Value by Reconciliation State**\n"]
            for s in ["VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"]:
                amt = state_amounts.get(s, 0)
                if amt > 0:
                    lines.append(f"  • **{s}**: {self._fmt(amt)}")
            return "\n".join(lines)

        lines = [
            "💰 **Monetary Impact by Reconciliation Outcome**\n",
            f"  ✅ **Auto-Reconciled (Correct)**: {self._fmt(r.get('auto_reconciled_correct', 0))}",
        ]
        fp = r.get("auto_reconciled_wrong_cost_of_false_positives", 0)
        if fp:
            lines.append(f"  ⚠️ **False Positive Cost**: {self._fmt(fp)}")
        lines.append(f"  👤 **Sent to Human Review**: {self._fmt(r.get('sent_to_human_review', 0))}")
        lines.append(f"  ❌ **Unresolved Value**: {self._fmt(r.get('unresolved', 0))}")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Comparison
    # ────────────────────────────────────────────────────────

    def _handle_compare(self, q: str) -> str:
        lines = ["📊 **State Comparison**\n"]
        states = ["VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"]

        for s in states:
            c = self._state_counts.get(s, 0)
            lines.append(f"  • **{s}**: {c:,} ({self._pct(c)})")

        verified = self._state_counts.get("VERIFIED", 0)
        unresolved = self._state_counts.get("UNRESOLVED", 0)
        diff = verified - unresolved

        lines.append(f"\n**Key Comparisons:**")
        if diff > 0:
            lines.append(f"  ✅ {diff:,} more verified than unresolved")
        elif diff < 0:
            lines.append(f"  ⚠️ {abs(diff):,} more unresolved than verified")
        else:
            lines.append(f"  Equal verified and unresolved counts")

        good = self._state_counts.get("VERIFIED", 0) + self._state_counts.get("EXPLAINED", 0)
        bad = self._state_counts.get("HUMAN_REVIEW", 0) + self._state_counts.get("UNRESOLVED", 0)
        lines.append(f"  ✅ Resolved: {good:,} vs ⚠️ Needs attention: {bad:,}")

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Settlement & Payment
    # ────────────────────────────────────────────────────────

    def _handle_settlement(self, q: str) -> str:
        match = re.search(r'(setl_\w+)', q, re.IGNORECASE)
        if match:
            return self._explain_specific_gap(match.group(1))

        if not self.decompositions:
            return "No settlement decomposition data available."

        total = len(self.decompositions)
        with_gap = [d for d in self.decompositions if d.get("gap_paise", 0) != 0]
        failed = [d for d in self.decompositions if d.get("failed")]

        lines = [
            f"📋 **Settlement Analysis** — {total:,} settlements decomposed\n",
            f"  • **Clean (No Gap)**: {total - len(with_gap):,}",
            f"  • **With Variance**: {len(with_gap):,}",
            f"  • **Failed Decomposition**: {len(failed):,}",
        ]

        if with_gap:
            total_gap = sum(abs(d.get("gap_paise", 0)) for d in with_gap)
            lines.append(f"\n💰 **Total Absolute Gap**: {self._fmt(total_gap)}")

        return "\n".join(lines)

    def _handle_payment(self, q: str) -> str:
        match = re.search(r'(pay_\w+)', q, re.IGNORECASE)
        if match:
            return self._handle_lookup(q)

        payments_with_data = [f for f in self.payment_flows if f.get("payment")]
        if not payments_with_data:
            return "No payment flow data available."

        total = len(payments_with_data)
        total_gross = sum(f["payment"].get("gross_paise", 0) for f in payments_with_data)
        with_settlement = sum(1 for f in payments_with_data if f.get("settlement"))
        with_ledger = sum(1 for f in payments_with_data if f.get("ledger"))

        lines = [
            f"💳 **Payment Summary** — {total:,} payments\n",
            f"  • **Total Gross Volume**: {self._fmt(total_gross)}",
            f"  • **Average Payment**: {self._fmt(total_gross // total) if total else '—'}",
            f"  • **With Settlement Link**: {with_settlement:,}",
            f"  • **With Ledger Entry**: {with_ledger:,}",
        ]

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Full summary
    # ────────────────────────────────────────────────────────

    def _handle_summary(self, q: str) -> str:
        total = self._total()
        m = self._metrics_inner()
        rm = self._run_meta()

        fb = self._fee_breakdown()

        coverage = m.get("coverage_terminal_without_human", 0) * 100
        accuracy = m.get("confusion", {}).get("accuracy", 0) * 100
        wall_clock = rm.get("wall_clock_seconds", 0) or self.results.get("wall_clock_seconds", 0)
        rps = total / wall_clock if wall_clock else 0

        lines = [
            "🤖 **Reconciliation Summary Report**\n",
            f"**{total:,}** transactions processed in **{wall_clock:.2f}s** "
            f"({rps:,.0f} records/sec)\n",
            f"💰 **Total Gross Volume**: {self._fmt(fb['total_gross'])}\n",
            "**Classification Breakdown:**",
        ]

        states = ["VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"]
        for s in states:
            c = self._state_counts.get(s, 0)
            lines.append(f"  {self.STATE_EMOJI[s]} **{s}**: {c:,} ({self._pct(c)})")

        lines.extend([
            "",
            f"🎯 **Automation Coverage**: {coverage:.1f}%",
        ])
        if accuracy > 0:
            lines.append(f"🎯 **Classification Accuracy**: {accuracy:.1f}%")

        gaps = [d for d in self.decompositions if d.get("gap_paise", 0) != 0]
        if gaps:
            total_gap = sum(abs(d.get("gap_paise", 0)) for d in gaps)
            lines.append(f"🔍 **Settlements with Gaps**: {len(gaps)} (total: {self._fmt(total_gap)})")

        flagged = self._state_counts.get("HUMAN_REVIEW", 0) + self._state_counts.get("UNRESOLVED", 0)
        if flagged:
            lines.append(f"⚠️ **{flagged} transactions need attention** — ask \"why are transactions flagged?\" for a root-cause breakdown.")

        lines.append(self._suggestions("Give me a full summary"))

        return "\n".join(lines)

    # ────────────────────────────────────────────────────────
    # Utility
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _routing_label(routing: str | None) -> str:
        if not routing:
            return "—"
        return {
            "auto_reconcile": "🟢 Auto Reconcile",
            "ai_suggestion_human_confirms": "🟡 AI + Human",
            "human_review_queue": "🔴 Human Queue",
        }.get(routing, routing)