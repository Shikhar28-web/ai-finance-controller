#!/usr/bin/env python3
"""One command: generate, normalize, reconcile, score. SPEC 10 step 6.

Usage: python tools/run_metrics.py [dev|sealed_eval|shift] [--json PATH]

Writes metrics_summary.json with the metrics/run_meta split required by SPEC 3: the
`metrics` block is what a garbage LLM must not change, and `run_meta` carries wall
clock, throughput and provenance, which a stub would legitimately change.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from afc import pipeline  # noqa: E402
from afc.config import (  # noqa: E402
    AUTO_THRESHOLD,
    HONESTY_CLAUSE,
    REVIEW_THRESHOLD,
    SEED_DEV,
    SEED_SEALED_EVAL,
    SEED_SEALED_EVAL_RESERVE,
    STATES,
)
from afc.core.confidence import AUTO_RECONCILE  # noqa: E402
from afc.core.decompose import DECOMPOSITION_FAILED  # noqa: E402
from afc.core.faults import DEV_ONLY_PAIRS  # noqa: E402
from afc.generate import emit, plan  # noqa: E402
from afc.generate.generator import generate  # noqa: E402
from afc.ingest.loader import load  # noqa: E402
from afc.llm.narrate import NullNarrator  # noqa: E402
from afc.metrics import attribution, confusion  # noqa: E402
from afc.money import format_rupees  # noqa: E402

DEV_ONLY_CLASSES = {"+".join(sorted(p)) for p in DEV_ONLY_PAIRS}
SPLITS = {
    "dev": (SEED_DEV, plan.DEFAULT),
    "sealed_eval": (SEED_SEALED_EVAL, plan.DEFAULT),
    "shift": (SEED_SEALED_EVAL_RESERVE[0], plan.SHIFTED),
}


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def build(split: str, narrator=None) -> dict:
    seed, targets = SPLITS[split]
    ds = generate(seed, targets)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        emit.emit(ds, out, targets_label=targets.label)
        batch = load(out)
    result = pipeline.run(batch)

    truth = {t.unit_id: t for t in ds.truth}
    predicted = {d.unit_id: d for d in result.decisions}
    impact = {t.unit_id: t.rupee_impact_paise for t in ds.truth}

    scored, excluded = [], 0
    rupees = Counter()
    for unit_id, t in sorted(truth.items()):
        if t.fault_class in DEV_ONLY_CLASSES:
            excluded += 1
            continue
        d = predicted.get(unit_id)
        if d is None:
            continue
        # Carry the fault class alongside the pair: deriving the fault-only subset by
        # zipping against a separately-sorted list silently misaligns true states with
        # fault classes when the two orderings differ.
        scored.append((t.true_state, d.state, t.fault_class))
        if d.routing == AUTO_RECONCILE:
            bucket = "auto_correct" if d.state == t.true_state else "auto_wrong"
            rupees[bucket] += impact[unit_id]
        elif d.state in ("HUMAN_REVIEW", "AWAITING_BANK"):
            rupees["review"] += impact[unit_id]
        else:
            rupees["unresolved"] += impact[unit_id]

    matrix = confusion.build([(t, p) for t, p, _ in scored])
    fault_only = confusion.build(
        [(t, p) for t, p, fc in scored if fc != "clean"]
    )

    attribution_score = attribution.score(result.decompositions, ds.settlement_truth)
    routing = Counter(d.routing for d in result.decisions)
    failures = sum(1 for d in result.decisions if d.rule == DECOMPOSITION_FAILED)
    terminal = sum(
        1 for d in result.decisions if d.state in ("VERIFIED", "EXPLAINED")
    )

    return {
        "metrics": {
            "split": split,
            "seed": seed,
            "targets": targets.label,
            "denominator": "recon_unit: one per payment, one per orphan bank credit, "
                           "one per payment-less settlement",
            "population_total": len(ds.truth),
            "population_scored": matrix.scored,
            "units_excluded_same_axis_compounds": excluded,
            "unkeyed_bank_rows": len(result.integrity_report.unkeyed_bank_rows),
            "decomposition_failures": failures,
            "confusion": matrix.as_dict(),
            "confusion_fault_carrying_only": fault_only.as_dict(),
            "attribution": attribution_score.as_dict(),
            "coverage_terminal_without_human": round(terminal / matrix.scored, 6)
            if matrix.scored else 0.0,
            "routing": dict(sorted(routing.items())),
            "thresholds": {"auto": AUTO_THRESHOLD, "review": REVIEW_THRESHOLD},
            "rupees": {
                "auto_reconciled_correct": rupees["auto_correct"],
                "auto_reconciled_wrong_cost_of_false_positives": rupees["auto_wrong"],
                "sent_to_human_review": rupees["review"],
                "unresolved": rupees["unresolved"],
            },
            "honesty_clause": HONESTY_CLAUSE,
        },
        "ai_note": (narrator or NullNarrator()).summarise({
            "confusion": matrix.as_dict()}).as_dict(),
        "run_meta": {
            "wall_clock_seconds": round(result.wall_clock_seconds, 4),
            "records_per_second": round(
                len(ds.truth) / result.wall_clock_seconds, 1)
            if result.wall_clock_seconds else 0.0,
            "git_sha": git_sha(),
            "generated_at_unix": int(time.time()),
        },
    }


def render(summary: dict) -> None:
    m = summary["metrics"]
    c = m["confusion"]
    print("=" * 78)
    print(f"METRICS  split={m['split']}  seed={m['seed']}  targets={m['targets']}")
    print("=" * 78)
    print(f"  denominator: {m['denominator']}")
    print(f"  population {m['population_total']}  scored {m['population_scored']}  "
          f"excluded {m['units_excluded_same_axis_compounds']} (same-axis compounds)")
    print(f"  unkeyed bank rows {m['unkeyed_bank_rows']}   "
          f"decomposition failures {m['decomposition_failures']}")
    print(f"\n  accuracy {c['accuracy']:.4f}  ({c['correct']}/{c['scored']})")
    print(f"\n  {'true \\ predicted':16}" + "".join(f"{s[:9]:>10}" for s in STATES))
    for label, row in zip(STATES, c["matrix_true_by_predicted"], strict=True):
        print(f"  {label:16}" + "".join(f"{n:>10}" for n in row))
    print(f"\n  {'state':16}{'support':>9}{'prec':>9}{'recall':>9}{'f1':>9}")
    for s, v in c["per_state"].items():
        print(f"  {s:16}{v['support']:>9}{v['precision']:>9.4f}"
              f"{v['recall']:>9.4f}{v['f1']:>9.4f}")
    a = m["attribution"]
    print(f"\n  exact_cause_set_match_rate {a['exact_cause_set_match_rate']:.4f}"
          f"  ({a['exact_cause_set_matches']}/{a['settlements_scored']})")
    print(f"  rupee_attribution_error    {a['rupee_attribution_error_paise']} paise"
          f"  ({a['rupee_attribution_error_rate']:.6f})")
    r = m["rupees"]
    print(f"\n  rupees auto-reconciled correct : {format_rupees(r['auto_reconciled_correct'])}")
    print("  cost of false positives        : "
          f"{format_rupees(r['auto_reconciled_wrong_cost_of_false_positives'])}")
    print(f"  sent to human review           : {format_rupees(r['sent_to_human_review'])}")
    print(f"  unresolved                     : {format_rupees(r['unresolved'])}")
    print(f"\n  coverage (terminal, no human)  : {m['coverage_terminal_without_human']:.4f}")
    print(f"  routing: {m['routing']}")
    rm = summary["run_meta"]
    print(f"\n  run_meta (excluded from the firewall diff): "
          f"{rm['wall_clock_seconds']}s, {rm['records_per_second']} rec/s, {rm['git_sha']}")
    print(f"\n  {m['honesty_clause'][:76]}")
    print(f"  {m['honesty_clause'][76:150]}...")
    print("  full clause in metrics_summary.json and README.md; see FINDINGS.md")


def main() -> int:
    split = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "dev"
    summary = build(split)
    render(summary)
    if "--json" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--json") + 1])
        path.write_text(json.dumps(summary, indent=2) + "\n")
        print(f"\n  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
