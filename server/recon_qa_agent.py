"""Reconciliation Q&A Agent.

Strict Q&A agent that answers user queries ONLY on the basis of the reconciliation
agent's outputs (decisions, decompositions, tax segregation, matching report, metrics).

It strictly enforces domain bounding: any question outside the reconciliation agent's
data and rules receives a clear out-of-scope response.
"""

from __future__ import annotations

import re
from afc.money import format_rupees


class ReconciliationQAAgent:
    """Q&A agent bound strictly to the reconciliation pipeline results."""

    def __init__(self, results: dict | None, metrics: dict | None):
        self.results = results or {}
        self.metrics = metrics or {}
        self.decisions = self.results.get("decisions", [])
        self.decompositions = self.results.get("decompositions", [])
        self.match_summary = self.results.get("match_summary", {})
        self.integrity = self.results.get("integrity", {})

    def answer(self, query: str) -> str:
        """Answer queries strictly derived from reconciliation agent data."""
        if not self.results:
            return (
                "⚠️ **No Reconciliation Agent Data Available**\n\n"
                "I am the Reconciliation Q&A Agent. I can only answer questions after "
                "the reconciliation agent has completed a run. Please run reconciliation first."
            )

        q = query.lower().strip()

        # 1. Tax & Fee Segregation Queries
        if any(kw in q for kw in ["tax", "gst", "mdr", "fee", "segregat", "deductuion", "rate", "drift"]):
            return self._tax_segregation_breakdown(q)

        # 2. Reconciliation Decisions & State Queries
        if any(kw in q for kw in ["verified", "explained", "awaiting", "human review", "unresolved", "state", "status"]):
            return self._state_breakdown(q)

        # 3. Gap Decomposition Queries
        if any(kw in q for kw in ["gap", "difference", "short", "variance", "attributed", "unexplained"]):
            return self._decomposition_breakdown(q)

        # 4. Integrity & Duplicate Findings
        if any(kw in q for kw in ["duplicate", "integrity", "defect", "orphan", "utr"]):
            return self._integrity_breakdown(q)

        # 5. Pipeline Accuracy & Performance Metrics
        if any(kw in q for kw in ["accuracy", "coverage", "metric", "confidence", "speed", "throughput"]):
            return self._metrics_breakdown(q)

        # 6. Overall Agent Summary
        if any(kw in q for kw in ["summary", "overview", "report", "all", "what did you find"]):
            return self._reconciliation_summary()

        # Fallback for out-of-bounds queries
        return (
            "🔒 **Strict Reconciliation Boundary Notice**\n\n"
            "As the dedicated Reconciliation Q&A Agent, I answer **exclusively** on the basis "
            "of the reconciliation agent's findings (Tax/Fee Segregation, Matching States, "
            "Gaps, Integrity Defects, and Confidence Metrics).\n\n"
            "Try asking me:\n"
            "• *\"Explain the tax and GST segregation breakdown\"*\n"
            "• *\"Why were transactions placed in HUMAN_REVIEW?\"*\n"
            "• *\"How is fee_rate_drift calculated for gaps?\"*\n"
            "• *\"What duplicate bank credits or ledger entries were flagged?\"*"
        )

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
            "**Core Tax & Fee Rules Applied by Reconciliation Agent:**",
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
            lines.append(f"• **{s}**: {c} units")

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
            f"• **Total Units Processed**: {total}\n"
            f"• **Tax & MDR Rules**: Contracted 1.90% MDR + 18% GST on Fee applied in integer paise.\n"
            f"• **Integrity Findings**: {self.integrity.get('bank_duplicate_groups', 0)} Bank duplicates, {self.integrity.get('ledger_duplicate_groups', 0)} Ledger duplicates.\n"
            f"• **Decompositions Evaluated**: {len(self.decompositions)} settlements analyzed for gap causes.\n\n"
            "Ask me any specific question regarding taxes, MDR drift, decisions, or gap breakdowns!"
        )
