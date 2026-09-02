"""Synthetic dataset generator. SPEC 4, SPEC 10 step 1.

A pure function of its seed. Dev and sealed_eval are the SAME code path with different
seeds -- no structural switch exists, because any structural difference between the
two would invalidate the measurement. The dev-only compound pairs are generated in
both splits and excluded at scoring time, never at generation time.

Money is integer paise; rates are integer basis points. The working-day calendar is a
declared closed world (see afc/core/holidays.py): the dataset asserts nothing about
real bank closures, so no settlement can land on a day the bank was "really" shut.

Ground truth records what was injected -- the true state, the fault class and the
rupee impact -- and is written from afc/core/faults.py, which predates the classifier.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, timedelta

from afc.config import CONTRACTED_MDR_BPS, GST_ON_FEE_BPS, VERIFIED
from afc.core.calendar import add_working_days, is_working_day
from afc.core.faults import (
    BY_MAGNITUDE,
    COMPOUND_PAIRS,
    SINGLE_FAULTS,
    state_for_bank_amount_mismatch,
)
from afc.core.holidays import holiday_set
from afc.core.models import (
    ORPHAN_BANK_CREDIT,
    PAYMENT,
    BankRow,
    Dataset,
    GroundTruth,
    LedgerEntry,
    Order,
    Payment,
    RateCard,
    Refund,
    Settlement,
    SettlementTruth,
)
from afc.generate import plan
from afc.money import net_per_payment_paise

T_PLUS = 2
LAG_SETTLEMENT_DAYS = 3  # the final working days, still inside their T+2 window


def _working_days(start: date, end: date, holidays: frozenset[date]) -> list[date]:
    out, day = [], start
    while day <= end:
        if is_working_day(day, holidays):
            out.append(day)
        day += timedelta(days=1)
    return out


def _amount(rng: random.Random) -> int:
    buckets = plan.AMOUNT_BUCKETS
    total = sum(w for _, _, w in buckets)
    pick = rng.randrange(total)
    for low, high, weight in buckets:
        if pick < weight:
            return rng.randrange(low, high, 100)  # whole rupees, in paise
        pick -= weight
    raise AssertionError("unreachable")


def _settlement_blueprints(t: plan.Targets) -> list[tuple[frozenset[str], dict[str, int]]]:
    """(settlement-level faults, {ledger fault or '': payment count}) for each group.

    Declared explicitly rather than derived, so the census can be checked against the
    plan line by line.
    """
    s, lg, c, d = t.settlement, t.ledger, t.compound, t.dev_only
    return [
        (frozenset(), {
            "": -1,  # clean settlements absorb the remainder; filled in below
            "refund_not_in_ledger": lg["refund_not_in_ledger"],
            "duplicate_ledger_entry": lg["duplicate_ledger_entry"],
        }),
        (frozenset({"fee_rate_drift"}), {
            "": s["fee_rate_drift"],
            "duplicate_ledger_entry": c[("fee_rate_drift", "duplicate_ledger_entry")],
            "refund_not_in_ledger": c[("fee_rate_drift", "refund_not_in_ledger")],
        }),
        (frozenset({"fee_rate_drift", "missing_utr"}), {
            "": c[("fee_rate_drift", "missing_utr")]}),
        (frozenset({"missing_utr"}), {
            "": s["missing_utr"],
            "duplicate_ledger_entry": c[("missing_utr", "duplicate_ledger_entry")]}),
        (frozenset({"bank_credit_missing"}), {"": s["bank_credit_missing"]}),
        (frozenset({"bank_amount_mismatch_material"}), {
            "": s["bank_amount_mismatch_material"],
            "duplicate_ledger_entry": c[("bank_amount_mismatch", "duplicate_ledger_entry")]}),
        (frozenset({"bank_amount_mismatch_bounded"}), {"": s["bank_amount_mismatch_bounded"]}),
        (frozenset({"duplicate_bank_credit"}), {
            "": s["duplicate_bank_credit"],
            "duplicate_ledger_entry": c[("duplicate_bank_credit", "duplicate_ledger_entry")]}),
        (frozenset({"timing_overdue"}), {
            "": s["timing_overdue"],
            "duplicate_ledger_entry": c[("timing_overdue", "duplicate_ledger_entry")]}),
        (frozenset({"timing_lag"}), {
            "": s["timing_lag"],
            "duplicate_ledger_entry": c[("timing_lag", "duplicate_ledger_entry")]}),
        (frozenset({"fee_rate_drift", "duplicate_bank_credit"}),
         {"": d[("fee_rate_drift", "duplicate_bank_credit")]}),
        (frozenset({"fee_rate_drift", "bank_amount_mismatch_material"}),
         {"": d[("fee_rate_drift", "bank_amount_mismatch")]}),
        (frozenset({"duplicate_bank_credit", "bank_amount_mismatch_material"}),
         {"": d[("duplicate_bank_credit", "bank_amount_mismatch")]}),
    ]


def _fault_class(settlement_faults: frozenset[str], ledger_fault: str) -> tuple[str, ...]:
    """Canonical fault tuple for a unit: settlement faults (normalised) plus ledger fault."""
    norm = sorted(
        f.replace("_material", "").replace("_bounded", "") for f in settlement_faults
    )
    return tuple(norm + ([ledger_fault] if ledger_fault else []))


def _true_state(faults: tuple[str, ...], settlement_faults: frozenset[str]) -> str:
    if not faults:
        return VERIFIED
    if len(faults) == 1:
        spec = SINGLE_FAULTS[faults[0]]
        if spec.true_state == BY_MAGNITUDE:
            delta = (
                plan.MATERIAL_MISMATCH_PAISE
                if "bank_amount_mismatch_material" in settlement_faults
                else plan.BOUNDED_MISMATCH_PAISE
            )
            return state_for_bank_amount_mismatch(delta)
        return spec.true_state
    pair = tuple(faults)
    for key in (pair, tuple(reversed(pair))):
        if key in COMPOUND_PAIRS:
            return COMPOUND_PAIRS[key].true_state
    return "DEV_ONLY"  # same-axis pair: excluded from sealed_eval scoring


def generate(seed: int, targets: plan.Targets = plan.DEFAULT) -> Dataset:
    rng = random.Random(seed)
    holidays = holiday_set(plan.WINDOW_START.year, plan.WINDOW_END.year)
    days = _working_days(plan.WINDOW_START, plan.WINDOW_END, holidays)
    lag_days, normal_days = days[-LAG_SETTLEMENT_DAYS:], days[:-LAG_SETTLEMENT_DAYS]

    blueprints = _settlement_blueprints(targets)
    declared = sum(n for _, mix in blueprints for k, n in mix.items() if n > 0)
    clean_count = plan.TARGET_ORDERS - declared
    if clean_count < 0:
        raise ValueError(f"plan over-subscribes {plan.TARGET_ORDERS} payments by {-clean_count}")
    blueprints[0][1][""] = clean_count

    # Split each blueprint's payments into realistically sized settlements.
    groups: list[tuple[frozenset[str], list[tuple[str, int]]]] = []
    for sfaults, mix in blueprints:
        items = [(lf, n) for lf, n in mix.items() if n > 0]
        total = sum(n for _, n in items)
        n_settlements = max(1, round(total / 12))
        buckets: list[list[tuple[str, int]]] = [[] for _ in range(n_settlements)]
        idx = 0
        for lf, n in items:
            for _ in range(n):
                buckets[idx % n_settlements].append((lf, 1))
                idx += 1
        for b in buckets:
            if b:
                groups.append((sfaults, b))

    lag_groups = [g for g in groups if "timing_lag" in g[0]]
    other_groups = [g for g in groups if "timing_lag" not in g[0]]
    rng.shuffle(other_groups)
    if len(other_groups) > len(normal_days):
        raise ValueError(
            f"{len(other_groups)} settlements need dates but only "
            f"{len(normal_days)} non-lag working days are available"
        )
    if len(lag_groups) > len(lag_days):
        raise ValueError(f"{len(lag_groups)} lag settlements but {len(lag_days)} lag days")

    ds = Dataset(
        seed=seed, as_of=plan.AS_OF,
        window_start=plan.WINDOW_START, window_end=plan.WINDOW_END,
        rate_card=RateCard(CONTRACTED_MDR_BPS, GST_ON_FEE_BPS),
        holidays=holidays,
    )
    scheduled = list(zip(other_groups, normal_days[: len(other_groups)], strict=True)) + list(
        zip(lag_groups, lag_days[: len(lag_groups)], strict=True)
    )

    n_pay = n_ord = n_ref = n_led = n_bank = 0
    for si, ((sfaults, members), settled_on) in enumerate(scheduled):
        sid = f"setl_{seed}_{si:04d}"
        utr = None if "missing_utr" in sfaults else f"UTR{seed}{si:06d}"
        due_on = add_working_days(settled_on, T_PLUS, holidays)
        ds.settlements.append(Settlement(sid, settled_on, utr, due_on))

        expected = 0
        for lf, _ in members:
            gross = _amount(rng)
            oid, pid = f"order_{seed}_{n_ord:05d}", f"pay_{seed}_{n_pay:05d}"
            n_ord += 1
            n_pay += 1
            mdr = plan.DRIFTED_MDR_BPS if "fee_rate_drift" in sfaults else CONTRACTED_MDR_BPS
            ds.orders.append(Order(oid, gross, settled_on))
            ds.payments.append(Payment(pid, oid, gross, settled_on, mdr, sid))
            net = net_per_payment_paise(gross, mdr, GST_ON_FEE_BPS)
            expected += net

            refunded = 0
            if rng.randrange(1000) < plan.REFUND_RATE_PER_MILLE or lf == "refund_not_in_ledger":
                refunded = min(gross, _amount(rng) // 2 or 10_000)
                ds.refunds.append(Refund(f"rfnd_{seed}_{n_ref:05d}", pid, refunded, settled_on))
                n_ref += 1
                expected -= refunded

            # Ledger books the net the merchant actually received.
            booked = net - (refunded if lf != "refund_not_in_ledger" else 0)
            ds.ledger.append(LedgerEntry(f"led_{seed}_{n_led:05d}", pid, booked, settled_on))
            n_led += 1
            if lf == "duplicate_ledger_entry":
                ds.ledger.append(LedgerEntry(f"led_{seed}_{n_led:05d}", pid, booked, settled_on))
                n_led += 1

            faults = _fault_class(sfaults, lf)
            state = _true_state(faults, sfaults)
            impact = 0
            if "fee_rate_drift" in faults:
                impact += net_per_payment_paise(gross, CONTRACTED_MDR_BPS, GST_ON_FEE_BPS) - net
            if lf == "duplicate_ledger_entry":
                impact += booked
            if lf == "refund_not_in_ledger":
                impact += refunded
            ds.truth.append(
                GroundTruth(pid, PAYMENT, state,
                            "clean" if not faults else "+".join(faults), faults, abs(impact))
            )

        # Bank side.
        credited = expected
        mismatch = 0
        if "bank_amount_mismatch_material" in sfaults:
            mismatch = plan.MATERIAL_MISMATCH_PAISE
        if "bank_amount_mismatch_bounded" in sfaults:
            mismatch = plan.BOUNDED_MISMATCH_PAISE
        credited -= mismatch
        absent = bool(sfaults & {"bank_credit_missing", "timing_lag", "timing_overdue"})
        if not absent:
            narration = f"NEFT/{utr or 'NA'}/RAZORPAY SETTLEMENT"
            ds.bank_rows.append(BankRow(f"bank_{seed}_{n_bank:05d}", due_on, narration, credited))
            n_bank += 1
            if "duplicate_bank_credit" in sfaults:
                ds.bank_rows.append(
                    BankRow(f"bank_{seed}_{n_bank:05d}", due_on, narration, credited)
                )
                n_bank += 1

            # Gap-axis ground truth, against the CONTRACTED baseline the decomposer
            # recomputes from (SPEC 6 step 1). Signed as contributions to
            # (actual - expected_at_contracted), so the parts sum to the gap.
            members_p = [p for p in ds.payments if p.settlement_id == sid]
            refunds_here = sum(
                r.amount_paise for r in ds.refunds
                if r.payment_id in {p.payment_id for p in members_p}
            )
            contracted = sum(
                net_per_payment_paise(p.gross_paise, CONTRACTED_MDR_BPS, GST_ON_FEE_BPS)
                for p in members_p
            ) - refunds_here
            actual_total = credited * (2 if "duplicate_bank_credit" in sfaults else 1)
            attribution: list[tuple[str, int]] = []
            if "fee_rate_drift" in sfaults:
                attribution.append(("fee_rate_drift", expected - contracted))
            if "duplicate_bank_credit" in sfaults:
                attribution.append(("duplicate_bank_credit", credited))
            ds.settlement_truth.append(SettlementTruth(
                settlement_id=sid,
                gap_faults=tuple(sorted(
                    f.replace("_material", "").replace("_bounded", "")
                    for f in sfaults
                    if f.startswith(("fee_rate_drift", "duplicate_bank_credit",
                                     "bank_amount_mismatch"))
                )),
                expected_gap_paise=actual_total - contracted,
                expected_attribution=tuple(attribution),
                expected_unexplained_paise=-mismatch,
            ))

    # Orphan bank credits: no settlement, no payment, their own recon units.
    for i in range(targets.orphans):
        bid = f"bank_{seed}_orph_{i:03d}"
        day = normal_days[rng.randrange(len(normal_days))]
        ds.bank_rows.append(
            BankRow(bid, day, f"NEFT/UTR{seed}ORPH{i:04d}/UNKNOWN CREDIT", _amount(rng))
        )
        ds.truth.append(
            GroundTruth(bid, ORPHAN_BANK_CREDIT, SINGLE_FAULTS["orphan_bank_credit"].true_state,
                        "orphan_bank_credit", ("orphan_bank_credit",), 0)
        )
    return replace(ds)
