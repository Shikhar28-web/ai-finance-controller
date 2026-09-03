"""AI Copilot for natural-language queries about reconciliation results.

The copilot answers questions using ONLY the pre-computed reconciliation data.
It never performs financial calculations — it reads the results and generates
human-readable responses.
"""

from __future__ import annotations

import re
from afc.money import format_rupees


def ask_copilot(question: str, results: dict, metrics: dict) -> str:
    """Answer a natural-language question about reconciliation results.

    Since we may not have an LLM API key, this uses pattern matching to handle
    common question types. Falls back to a summary response.
    """
    q = question.lower().strip()

    decisions = results.get("decisions", [])
    decompositions = results.get("decompositions", [])

    # Count states
    state_counts = {}
    state_amounts = {}
    for d in decisions:
        s = d.get("state", "UNKNOWN")
        state_counts[s] = state_counts.get(s, 0) + 1
        # We don't always have amount in decision, so count is primary

    # ---- Question patterns ----

    # Summary / overview
    if any(kw in q for kw in ["summary", "overview", "status", "how many", "total", "all"]):
        return _build_summary(decisions, metrics)

    # Unresolved
    if "unresolved" in q:
        return _filter_by_state(decisions, "UNRESOLVED", "unresolved")

    # Human review
    if "human" in q or "review" in q:
        return _filter_by_state(decisions, "HUMAN_REVIEW", "requiring human review")

    # Verified
    if "verified" in q or "clean" in q or "correct" in q:
        return _filter_by_state(decisions, "VERIFIED", "verified")

    # Explained
    if "explained" in q:
        return _filter_by_state(decisions, "EXPLAINED", "explained (differences fully attributed)")

    # Awaiting bank
    if "awaiting" in q or "pending" in q or "waiting" in q:
        return _filter_by_state(decisions, "AWAITING_BANK", "awaiting bank credit")

    # Short / difference / mismatch for a specific settlement or payment
    if "short" in q or "differ" in q or "mismatch" in q or "gap" in q:
        # Try to find a specific ID
        id_match = re.search(r'(setl_\w+|pay_\w+|settle?ment[_ ]\w+)', q, re.IGNORECASE)
        if id_match:
            return _explain_gap(id_match.group(1), decompositions)
        return _explain_all_gaps(decompositions)

    # Confidence
    if "confidence" in q:
        return _confidence_summary(decisions)

    # Money / amount
    if "money" in q or "amount" in q or "rupee" in q or "₹" in q:
        return _money_summary(metrics)

    # Accuracy
    if "accuracy" in q or "precise" in q or "correct" in q:
        return _accuracy_summary(metrics)

    # Exceptions
    if "exception" in q or "issue" in q or "problem" in q or "error" in q:
        return _exceptions_summary(decisions)

    # Default: give a general summary
    return _build_summary(decisions, metrics)


def _build_summary(decisions: list[dict], metrics: dict) -> str:
    counts = {}
    for d in decisions:
        s = d.get("state", "UNKNOWN")
        counts[s] = counts.get(s, 0) + 1

    total = len(decisions)
    lines = [f"📊 **Reconciliation Summary** — {total} transactions processed\n"]

    state_emoji = {
        "VERIFIED": "✅",
        "EXPLAINED": "🔍",
        "AWAITING_BANK": "⏳",
        "HUMAN_REVIEW": "👤",
        "UNRESOLVED": "❌",
    }

    for state, emoji in state_emoji.items():
        count = counts.get(state, 0)
        pct = f"{count/total*100:.1f}%" if total > 0 else "0%"
        lines.append(f"{emoji} **{state}**: {count} ({pct})")

    m = metrics.get("metrics", metrics)
    if "coverage_terminal_without_human" in m:
        cov = m["coverage_terminal_without_human"]
        lines.append(f"\n📈 Coverage (auto-resolved without human): **{cov*100:.1f}%**")

    if "confusion" in m:
        acc = m["confusion"].get("accuracy", 0)
        lines.append(f"🎯 Classification accuracy: **{acc*100:.1f}%**")

    return "\n".join(lines)


def _filter_by_state(decisions: list[dict], state: str, label: str) -> str:
    matching = [d for d in decisions if d.get("state") == state]
    if not matching:
        return f"✅ No transactions are currently {label}."

    lines = [f"Found **{len(matching)}** transactions {label}:\n"]
    for d in matching[:20]:  # Limit to 20
        unit_id = d.get("unit_id", "?")
        rule = d.get("rule", "?")
        conf = d.get("confidence", {}).get("score", 0)
        routing = d.get("routing", "?")
        lines.append(f"• `{unit_id}` — Rule {rule}, Confidence {conf:.2f}, Routing: {routing}")

    if len(matching) > 20:
        lines.append(f"\n... and {len(matching) - 20} more.")

    return "\n".join(lines)


def _explain_gap(settlement_id: str, decompositions: list[dict]) -> str:
    for d in decompositions:
        if settlement_id in d.get("settlement_id", ""):
            gap = d.get("gap_paise", 0)
            expected = d.get("expected_paise", 0)
            actual = d.get("actual_paise", 0)
            unexplained = d.get("unexplained_paise", 0)
            attributed = d.get("attributed", [])

            lines = [f"🔍 **Gap Analysis for {d['settlement_id']}**\n"]
            lines.append(f"Expected (contracted rate): **{format_rupees(expected)}**")
            if actual is not None:
                lines.append(f"Actual bank credit: **{format_rupees(actual)}**")
            lines.append(f"Gap: **{format_rupees(gap)}**\n")

            if attributed:
                lines.append("**Attributed causes:**")
                for a in attributed:
                    lines.append(f"• {a['cause']}: {format_rupees(a['amount_paise'])} — {a.get('proof', '')}")

            lines.append(f"\nUnexplained: **{format_rupees(unexplained)}**")
            return "\n".join(lines)

    return f"No decomposition found for settlement matching '{settlement_id}'."


def _explain_all_gaps(decompositions: list[dict]) -> str:
    with_gap = [d for d in decompositions if d.get("gap_paise", 0) != 0]
    if not with_gap:
        return "✅ No gaps found — all settlements match exactly."

    lines = [f"Found **{len(with_gap)}** settlements with gaps:\n"]
    for d in with_gap[:15]:
        gap = d.get("gap_paise", 0)
        causes = [a["cause"] for a in d.get("attributed", [])]
        cause_str = ", ".join(causes) if causes else "unexplained"
        lines.append(f"• `{d['settlement_id']}`: Gap {format_rupees(gap)} — {cause_str}")

    if len(with_gap) > 15:
        lines.append(f"\n... and {len(with_gap) - 15} more.")

    return "\n".join(lines)


def _confidence_summary(decisions: list[dict]) -> str:
    scores = [d.get("confidence", {}).get("score", 0) for d in decisions]
    if not scores:
        return "No confidence data available."

    avg = sum(scores) / len(scores)
    high = sum(1 for s in scores if s >= 0.9)
    mid = sum(1 for s in scores if 0.6 <= s < 0.9)
    low = sum(1 for s in scores if s < 0.6)

    return (
        f"📊 **Confidence Distribution** ({len(scores)} transactions)\n\n"
        f"• High (≥ 0.9): **{high}** — eligible for auto-reconciliation\n"
        f"• Medium (0.6 – 0.9): **{mid}** — AI suggestion, human confirms\n"
        f"• Low (< 0.6): **{low}** — straight to human queue\n"
        f"• Average confidence: **{avg:.3f}**"
    )


def _money_summary(metrics: dict) -> str:
    m = metrics.get("metrics", metrics)
    r = m.get("rupees", {})
    lines = ["💰 **Financial Summary**\n"]
    if r.get("auto_reconciled_correct", 0):
        lines.append(f"• Auto-reconciled correctly: {format_rupees(r['auto_reconciled_correct'])}")
    if r.get("auto_reconciled_wrong_cost_of_false_positives", 0):
        lines.append(f"• ⚠️ Cost of false positives: {format_rupees(r['auto_reconciled_wrong_cost_of_false_positives'])}")
    if r.get("sent_to_human_review", 0):
        lines.append(f"• Sent to human review: {format_rupees(r['sent_to_human_review'])}")
    if r.get("unresolved", 0):
        lines.append(f"• Unresolved: {format_rupees(r['unresolved'])}")
    return "\n".join(lines)


def _accuracy_summary(metrics: dict) -> str:
    m = metrics.get("metrics", metrics)
    c = m.get("confusion", {})
    acc = c.get("accuracy", 0)
    scored = c.get("scored", 0)
    correct = c.get("correct", 0)

    return (
        f"🎯 **Classification Accuracy**: {acc*100:.1f}% ({correct}/{scored} units)\n\n"
        f"⚠️ Note: {m.get('honesty_clause', 'See metrics for qualification details.')[:200]}"
    )


def _exceptions_summary(decisions: list[dict]) -> str:
    exceptions = [
        d for d in decisions
        if d.get("state") in ("HUMAN_REVIEW", "UNRESOLVED")
    ]
    if not exceptions:
        return "✅ No exceptions found — all transactions are verified or explained."

    lines = [f"⚠️ **Exception Summary** — {len(exceptions)} transactions need attention\n"]

    hr = [d for d in exceptions if d.get("state") == "HUMAN_REVIEW"]
    ur = [d for d in exceptions if d.get("state") == "UNRESOLVED"]

    if hr:
        lines.append(f"👤 **Human Review** ({len(hr)}):")
        for d in hr[:10]:
            lines.append(f"  • `{d['unit_id']}` — Rule {d.get('rule', '?')}, Confidence {d.get('confidence', {}).get('score', 0):.2f}")
        if len(hr) > 10:
            lines.append(f"  ... and {len(hr) - 10} more")

    if ur:
        lines.append(f"\n❌ **Unresolved** ({len(ur)}):")
        for d in ur[:10]:
            lines.append(f"  • `{d['unit_id']}` — Rule {d.get('rule', '?')}, Confidence {d.get('confidence', {}).get('score', 0):.2f}")
        if len(ur) > 10:
            lines.append(f"  ... and {len(ur) - 10} more")

    return "\n".join(lines)
