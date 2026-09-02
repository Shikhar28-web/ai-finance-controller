"""Orchestration: normalized batch -> Level 0 -> matching -> decompose -> decide.

Unguarded on purpose: this module is allowed to touch storage and the clock, because
timing lives in run_meta and never in a classification path. The guarded packages it
calls cannot reach either.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from afc.core import decide as decide_mod
from afc.core import decompose as decompose_mod
from afc.core import integrity, matching
from afc.core.models import ORPHAN_BANK_CREDIT, PAYMENT
from afc.ingest.loader import NormalizedBatch


@dataclass(frozen=True)
class PipelineResult:
    decisions: tuple[decide_mod.Decision, ...]
    decompositions: tuple[decompose_mod.Decomposition, ...]
    integrity_report: integrity.IntegrityReport
    match_report: matching.MatchReport
    wall_clock_seconds: float


def run(batch: NormalizedBatch) -> PipelineResult:
    started = time.perf_counter()

    report = integrity.run(batch.ledger, batch.bank_rows, batch.utr_by_bank_row)
    matches = matching.run(
        batch.payments, batch.settlements, batch.ledger, batch.refunds,
        batch.bank_rows, batch.utr_by_bank_row, report, batch.holidays,
    )
    decompositions = decompose_mod.decompose_all(
        matches.level3, batch.payments, batch.refunds, batch.bank_rows, batch.rate_card
    )

    level1_by_payment = {r.payment_id: r for r in matches.level1}
    level3_by_settlement = {r.settlement_id: r for r in matches.level3}
    decomposition_by_settlement = {d.settlement_id: d for d in decompositions}
    due_by_settlement = {s.settlement_id: s.due_on for s in batch.settlements}
    duplicated_utrs = report.flagged_utrs
    settlement_utr = {s.settlement_id: s.utr for s in batch.settlements}

    settlements_with_duplicate = {
        sid for sid, utr in settlement_utr.items() if utr in duplicated_utrs
    }
    by_settlement: dict[str, list[str]] = defaultdict(list)
    for p in batch.payments:
        if p.settlement_id:
            by_settlement[p.settlement_id].append(p.payment_id)

    decisions = []
    for p in sorted(batch.payments, key=lambda x: x.payment_id):
        sid = p.settlement_id
        defect = (
            p.payment_id in report.flagged_payment_ids
            or sid in settlements_with_duplicate
        )
        decisions.append(decide_mod.decide(decide_mod.Evidence(
            unit_id=p.payment_id,
            unit_kind=PAYMENT,
            level1=level1_by_payment.get(p.payment_id),
            level3=level3_by_settlement.get(sid) if sid else None,
            decomposition=decomposition_by_settlement.get(sid) if sid else None,
            due_on=due_by_settlement.get(sid) if sid else None,
            as_of=batch.as_of,
            integrity_defect=defect,
        )))

    for bank_row_id in matches.orphan_bank_row_ids:
        decisions.append(decide_mod.decide(decide_mod.Evidence(
            unit_id=bank_row_id, unit_kind=ORPHAN_BANK_CREDIT,
            level1=None, level3=None, decomposition=None, due_on=None,
            as_of=batch.as_of, integrity_defect=False,
        )))

    return PipelineResult(
        decisions=tuple(sorted(decisions, key=lambda d: d.unit_id)),
        decompositions=decompositions,
        integrity_report=report,
        match_report=matches,
        wall_clock_seconds=time.perf_counter() - started,
    )
