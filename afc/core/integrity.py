"""Level 0: integrity findings, run before any matching. SPEC 5, SPEC 7 R7/R9.

SPEC 5 declares Level 1 and Level 3 as 1:1 joins, but duplicate_ledger_entry produces
1:2 and duplicate_bank_credit produces a UTR that returns two bank rows. A 1:1 join
would silently pick one row and drop the other -- dedup by accident -- and the fault
it was graded on would never be detected. So duplicates are found first, by grouping
and asserting cardinality, and `.first()` is banned in matcher code (enforced by
tools/check_imports.py).

COUNTING, PINNED. Three different numbers get called "duplicates" and only one of
them is a duplicate count:

    groups         keys appearing more than once
    excess_rows    rows in those groups, minus one per group
    group_members  every row in those groups

For a duplication factor of exactly 2 the first two coincide, which is how an
ambiguous count survives review. Level 0 emits ONE FINDING PER GROUP. The recon units
it flags equal the affected payments (ledger side) or affected settlements (bank
side) -- never group_members, which would double-count and pull clean records into
HUMAN_REVIEW through the R7 ledger clause, an error that reads like a classifier
result rather than a counting bug.

BANK ROWS ARE GROUPED BY EXTRACTED UTR, NEVER BY NARRATION. Every missing_utr
settlement writes the same narration text, so grouping on narration merges unrelated
settlements into one phantom duplicate group. Rows with no extractable UTR cannot be
keyed at all; they are excluded from the check and reported, not silently dropped.

FINDINGS CARRY THEIR AXIS. duplicate_ledger_entry and duplicate_bank_credit both end
at HUMAN_REVIEW but by different SPEC 7 clauses -- the ledger clause on R7, and the
integrity-defect amendment on R9. An undifferentiated "duplicate found" flag would
leave the cascade unable to pick the right rule, and the step 5 diff would report a
plumbing gap as a mapping disagreement.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from afc.core.faults import GAP, LEDGER
from afc.core.models import BankRow, LedgerEntry

DUPLICATE_LEDGER_ENTRY = "duplicate_ledger_entry"
DUPLICATE_BANK_CREDIT = "duplicate_bank_credit"


@dataclass(frozen=True)
class DuplicateFinding:
    axis: str                    # LEDGER -> SPEC 7 R7; GAP -> the R9 integrity amendment
    kind: str
    key: str                     # payment_id (ledger) or UTR (bank)
    row_ids: tuple[str, ...]
    excess_paise: int            # money implied by the copies beyond the first

    @property
    def group_members(self) -> int:
        return len(self.row_ids)

    @property
    def excess_rows(self) -> int:
        return len(self.row_ids) - 1


@dataclass(frozen=True)
class IntegrityReport:
    ledger_duplicates: tuple[DuplicateFinding, ...]
    bank_duplicates: tuple[DuplicateFinding, ...]
    unkeyed_bank_rows: tuple[str, ...]

    @property
    def flagged_payment_ids(self) -> frozenset[str]:
        return frozenset(f.key for f in self.ledger_duplicates)

    @property
    def flagged_utrs(self) -> frozenset[str]:
        return frozenset(f.key for f in self.bank_duplicates)

    def summary(self) -> dict[str, int]:
        return {
            "ledger_duplicate_groups": len(self.ledger_duplicates),
            "ledger_excess_rows": sum(f.excess_rows for f in self.ledger_duplicates),
            "ledger_group_members": sum(f.group_members for f in self.ledger_duplicates),
            "bank_duplicate_groups": len(self.bank_duplicates),
            "bank_excess_rows": sum(f.excess_rows for f in self.bank_duplicates),
            "bank_group_members": sum(f.group_members for f in self.bank_duplicates),
            "unkeyed_bank_rows": len(self.unkeyed_bank_rows),
        }


def find_ledger_duplicates(ledger: list[LedgerEntry]) -> tuple[DuplicateFinding, ...]:
    """One finding per payment_id booked more than once."""
    groups: dict[str, list[LedgerEntry]] = defaultdict(list)
    for entry in ledger:
        groups[entry.payment_id].append(entry)
    out = []
    for payment_id, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda e: e.ledger_id)
        out.append(
            DuplicateFinding(
                axis=LEDGER,
                kind=DUPLICATE_LEDGER_ENTRY,
                key=payment_id,
                row_ids=tuple(e.ledger_id for e in ordered),
                excess_paise=sum(e.amount_paise for e in ordered[1:]),
            )
        )
    return tuple(out)


def find_bank_duplicates(
    bank_rows: list[BankRow], utr_by_bank_row: dict[str, str | None]
) -> tuple[tuple[DuplicateFinding, ...], tuple[str, ...]]:
    """One finding per UTR credited more than once, plus the rows that cannot be keyed."""
    groups: dict[str, list[BankRow]] = defaultdict(list)
    unkeyed: list[str] = []
    for row in bank_rows:
        utr = utr_by_bank_row.get(row.bank_row_id)
        if utr is None:
            unkeyed.append(row.bank_row_id)
            continue
        groups[utr].append(row)
    out = []
    for utr, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda b: b.bank_row_id)
        out.append(
            DuplicateFinding(
                axis=GAP,
                kind=DUPLICATE_BANK_CREDIT,
                key=utr,
                row_ids=tuple(b.bank_row_id for b in ordered),
                excess_paise=sum(b.credit_paise for b in ordered[1:]),
            )
        )
    return tuple(out), tuple(sorted(unkeyed))


def run(
    ledger: list[LedgerEntry],
    bank_rows: list[BankRow],
    utr_by_bank_row: dict[str, str | None],
) -> IntegrityReport:
    bank, unkeyed = find_bank_duplicates(bank_rows, utr_by_bank_row)
    return IntegrityReport(
        ledger_duplicates=find_ledger_duplicates(ledger),
        bank_duplicates=bank,
        unkeyed_bank_rows=unkeyed,
    )
