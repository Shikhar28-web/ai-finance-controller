"""Reconciliation Q&A Agent.

Strict, natural-language Q&A agent that answers user queries based on the
reconciliation pipeline's outputs, money totals, tax calculations, matching
states, and integrity findings.
"""

from __future__ import annotations

import re
from afc.money import format_rupees


class ReconciliationQAAgent:
    """Q&A agent bound to the reconciliation pipeline results."""

    def __init__(self, results: dict | None, metrics: dict | None):
        self.results = results or {}
        self.metrics = metrics or {}
        self.decisions = self.results.get("decisions", [])
        self.decompositions = self.results.get("decompositions", [])
        self.payment_flows = self.results.get("payment_flows", [])
        self.match_summary = self.results.get("match_summary", {})
        self.integrity = self.results.get("integrity", {})

    def answer(self, query: str) -> str:
        """Answer queries using data from the reconciliation run."""
        if not self.results:
            return (
                "⚠️ **No Reconciliation Data Available**\n\n"
                "Please run a reconciliation first (using live Razorpay data or demo synthetic data). "
                "Once completed, I can break down amounts, fees, GST, matches, and exceptions for you!"
            )

        q = query.lower().strip()

        # 1. Total Amount / Paid / Volume / Financial Totals
        if any(kw in q for kw in ["paid", "pay", "amount", "total", "volume", "gross", "net", "how much", "receive", "credited", "payout"]):
            return self._financial_amounts_breakdown(q)

        # 2. Tax & Fee Segregation Queries
        if any(kw in q for kw in ["tax", "gst", "mdr", "fee", "rate", "drift", "deduct"]):
            return self._tax_segregation_breakdown(q)

        # 3. Reconciliation Decisions & State Queries
        if any(kw in q for kw in ["verified", "explained", "awaiting", "human review", "unresolved", "state", "status", "queue"]):
            return self._state_breakdown(q)

        # 4. Gap Decomposition Queries
        if any(kw in q for kw in ["gap", "difference", "short", "variance", "attributed", "unexplained"]):
            return self._decomposition_breakdown(q)

        # 5. Integrity & Duplicate Findings
        if any(kw in q for kw in ["duplicate", "integrity", "defect", "orphan", "utr"]):
            return self._integrity_breakdown(q)

        # 6. Pipeline Accuracy & Performance Metrics
        if any(kw in q for kw in ["accuracy", "coverage", "metric", "confidence", "speed", "throughput"]):
            return self._metrics_breakdown(q)

        # 7. Overall Summary
        if any(kw in q for kw in ["summary", "overview", "report", "all", "what did you find", "help"]):
            return self._reconciliation_summary()

        # Catch-all: default to financial breakdown + summary if money is asked or general query
        return self._financial_amounts_breakdown(q)

    def _financial_amounts_breakdown(self, q: str) -> str:
        """Aggregates and formats all monetary amounts from payment flows and decompositions."""
        total_gross_paise = 0
        total_ledger_paise = 0
        payments_count = 0

        for f in self.payment_flows:
            p = f.get("payment")
            if p:
                total_gross_paise += p.get("gross_paise", 0)
                payments_count += 1
            l = f.get("ledger")
            if l:
                total_ledger_paise += l.get("amount_paise", 0)

        # Calculate MDR (1.90%) and GST (18% of MDR = 0.342%)
        total_mdr_paise = int(total_gross_paise * 0.019)
        total_gst_paise = int(total_mdr_paise * 0.18)
        total_fees_paise = total_mdr_paise + total_gst_paise
        estimated_net_paise = total_gross_paise - total_fees_paise

        # Actual bank credits across decompositions
        actual_bank_paise = sum(
            d.get("actual_paise", 0) or 0 for d in self.decompositions
        )

        m = self.metrics.get("metrics", self.metrics)
        rupees = m.get("rupees", {})

        auto_rec = rupees.get("auto_reconciled_correct", 0)
        review_amt = rupees.get("sent_to_human_review", 0)
        unres_amt = rupees.get("unresolved", 0)

        lines = [
            "💰 **Financial & Transaction Volume Summary**\n",
            f"• **Total Payments Processed**: **{payments_count}** transactions",
            f"• **Total Gross Volume**: **{format_rupees(total_gross_paise)}**",
            f"• **Est. MDR Fees (1.90%)**: **{format_rupees(total_mdr_paise)}**",
            f"• **Est. GST on MDR (18%)**: **{format_rupees(total_gst_paise)}**",
            f"• **Total Fee & Tax Deductions**: **{format_rupees(total_fees_paise)}**",
            f"• **Net Expected Payout**: **{format_rupees(estimated_net_paise)}**\n",
        ]

        if actual_bank_paise > 0:
            lines.append(f"🏦 **Actual Bank Credits Received**: **{format_rupees(actual_bank_paise)}**")

        if auto_rec or review_amt or unres_amt:
            lines.extend([
                "\n📌 **Money Breakdown by Reconciliation Status:**",
                f"• ✅ **Auto-Reconciled Value**: **{format_rupees(auto_rec)}**",
                f"• 👤 **Sent to Human Review Queue**: **{format_rupees(review_amt)}**",
                f"• ❌ **Unresolved Gap Value**: **{format_rupees(unres_amt)}**",
            ])

        return "\n".join(lines)

    def _tax_segregation_breakdown(self, q: str) -> str:
        """Detailed breakdown of Tax & MDR Fee segregation from the recon run."""
        contracted_mdr_bps = 190  # 1.90%
        gst_bps = 1800           # 18.00% GST on Fee

        drifts = []
        for d in self.decompositions:
            for attr in d.get("attributed", []):
                if attr.get("cause") == "fee_rate_drift":
                    drifts.append((d.get("settlement_id"), attr.get("amount_paise", 0), attr.get("proof", "")))

        lines = [
            "🧾 **Tax & MDR Fee Segregation Report**\n",
            "**Core Tax & Fee Rules Applied by Reconciliation Engine:**",
            "1. **MDR Fee (Merchant Discount Rate)**: Calculated per payment as `round_half_away_from_zero(Gross * MDR_BPS / 10000)`.",
            f"   • Contracted MDR: **{contracted_mdr_bps/100:.2f}%** ({contracted_mdr_bps} BPS)",
            "2. **GST on Fee**: Applied **strictly on the MDR fee**, NOT on the gross transaction amount.",
            f"   • Statutory GST Rate: **{gst_bps/100:.2f}%** ({gst_bps} BPS)",
            "   • Calculation: `round_half_away_from_zero(MDR_Fee * 1800 / 10000)`",
            "3. **Net Payout Formula**: `Net Bank Payout = Gross Amount - MDR Fee - GST on MDR Fee`",
            "4. **Integer Paise Integrity**: All tax & fee computations are locked in integer paise to eliminate floating-point rounding errors.\n",
        ]

        if drifts:
            lines.append(f"⚠️ **Detected Fee Rate Drifts ({len(drifts)} settlements affected):**")
            for setl_id, amt, proof in drifts[:5]:
                lines.append(f"• `{setl_id}`: Variance of **{format_rupees(amt)}** — {proof}")
            if len(drifts) > 5:
                lines.append(f"  ...and {len(drifts) - 5} more settlements with fee drift.")
        else:
            lines.append("✅ **No Fee Rate Drifts Detected**: All settlements strictly match the contracted MDR rate.")

        return "\n".join(lines)

    def _state_breakdown(self, q: str) -> str:
        counts = {}
        for d in self.decisions:
            s = d.get("state", "UNKNOWN")
            counts[s] = counts.get(s, 0) + 1

        lines = ["📊 **Reconciliation Agent Decision Breakdown**\n"]
        states = ["VERIFIED", "EXPLAINED", "AWAITING_BANK", "HUMAN_REVIEW", "UNRESOLVED"]
        for s in states:
            c = counts.get(s, 0)
            lines.append(f"• **{s}**: **{c}** units")

        return "\n".join(lines)

    def _decomposition_breakdown(self, q: str) -> str:
        gaps = [d for d in self.decompositions if d.get("gap_paise", 0) != 0]
        if not gaps:
            return "✅ **No Gaps Found**: All bank credits matched expected settlement net payouts exactly."

        lines = [f"🔍 **Settlement Gap Attributions ({len(gaps)} settlements)**\n"]
        for g in gaps[:10]:
            setl_id = g.get("settlement_id")
            gap = g.get("gap_paise", 0)
            unexp = g.get("unexplained_paise", 0)
            attrs = g.get("attributed", [])
            attr_str = ", ".join([f"{a['cause']}: {format_rupees(a['amount_paise'])}" for a in attrs]) or "None"
            lines.append(
                f"• Settlement `{setl_id}`:\n"
                f"  - Total Gap: **{format_rupees(gap)}**\n"
                f"  - Attributed Causes: {attr_str}\n"
                f"  - Unexplained Residual: **{format_rupees(unexp)}**"
            )
        return "\n".join(lines)

    def _integrity_breakdown(self, q: str) -> str:
        rep = self.integrity
        return (
            "🛡️ **Level 0 Integrity Report**\n\n"
            f"• Ledger Duplicate Groups: **{rep.get('ledger_duplicate_groups', 0)}**\n"
            f"• Ledger Excess Rows: **{rep.get('ledger_excess_rows', 0)}**\n"
            f"• Bank Duplicate Groups: **{rep.get('bank_duplicate_groups', 0)}**\n"
            f"• Bank Excess Rows: **{rep.get('bank_excess_rows', 0)}**\n"
            f"• Unkeyed Bank Rows: **{rep.get('unkeyed_bank_rows', 0)}**"
        )

    def _metrics_breakdown(self, q: str) -> str:
        m = self.metrics.get("metrics", self.metrics)
        cov = m.get("coverage_terminal_without_human", 0) * 100
        conf = m.get("confusion", {})
        acc = conf.get("accuracy", 0) * 100
        return (
            "🎯 **Reconciliation Agent Performance & Accuracy**\n\n"
            f"• Auto-Resolution Coverage: **{cov:.1f}%**\n"
            f"• Classification Accuracy: **{acc:.1f}%**\n"
            f"• Total Units Scored: **{m.get('population_scored', len(self.decisions))}**\n"
            f"• Decomposition Failures: **{m.get('decomposition_failures', 0)}**"
        )

    def _reconciliation_summary(self) -> str:
        total = len(self.decisions)
        return (
            f"🤖 **Reconciliation Agent Work Summary**\n\n"
            f"• **Total Units Processed**: **{total}**\n"
            f"• **Tax & MDR Rules**: Contracted 1.90% MDR + 18% GST on Fee applied in integer paise.\n"
            f"• **Integrity Findings**: {self.integrity.get('bank_duplicate_groups', 0)} Bank duplicates, {self.integrity.get('ledger_duplicate_groups', 0)} Ledger duplicates.\n"
            f"• **Decompositions Evaluated**: {len(self.decompositions)} settlements analyzed for gap causes.\n\n"
            "Ask me any question regarding your total volume, MDR fees, GST, decisions, or settlement gaps!"
        )
