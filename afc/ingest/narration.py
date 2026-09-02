"""UTR extraction from bank narration, with a loud failure on partial coverage.

This is the one place in the pipeline where a silent partial failure is plausible.
A regex that matches 90% of rows and quietly drops the rest does not look like a bug:
it looks like missing_utr faults nobody injected, and it inflates the inferred-match
path and HUMAN_REVIEW at the same time. So extraction is never allowed to return a
blank and move on -- coverage is asserted against a known expected count and any
shortfall raises.

Note this does not violate SPEC 3's "match on numbers and dates, not fuzzy strings".
Parsing a structurally-formatted token out of a narration is exact: the pattern either
matches or it does not, and non-matches are surfaced rather than approximated. No
matching decision anywhere in the pipeline is made on narration similarity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A UTR is a fixed-shape token. Anchored to a delimiter boundary so it cannot pick up
# a substring of a longer reference number.
UTR_PATTERN = re.compile(r"(?:^|[/\s|:-])(UTR[A-Z0-9]{6,})(?=$|[/\s|:-])")


class NarrationCoverageError(RuntimeError):
    """Raised when UTR extraction covers fewer rows than expected."""


@dataclass(frozen=True)
class Extraction:
    utr: str | None
    narration: str


def extract_utr(narration: str) -> str | None:
    """Exactly one UTR, or None. Two matches is ambiguous and is treated as no match."""
    found = UTR_PATTERN.findall(narration.upper())
    if len(found) != 1:
        return None
    return found[0]


def extract_all(narrations: list[str], expected_with_utr: int) -> list[Extraction]:
    """Extract across a statement and refuse to proceed on a coverage shortfall.

    `expected_with_utr` is the number of rows that genuinely carry a UTR, known from
    the generator's manifest. A shortfall means the regex is dropping rows, and the
    resulting blanks would be indistinguishable from injected missing_utr faults.
    """
    out = [Extraction(extract_utr(n), n) for n in narrations]
    matched = sum(1 for e in out if e.utr is not None)
    if matched < expected_with_utr:
        missed = [e.narration for e in out if e.utr is None][:5]
        raise NarrationCoverageError(
            f"UTR extraction covered {matched}/{expected_with_utr} rows that carry a "
            f"UTR ({matched / expected_with_utr:.1%}). A shortfall is indistinguishable "
            f"from injected missing_utr faults and would inflate the inferred-match "
            f"path. First unmatched narrations: {missed}"
        )
    if matched > expected_with_utr:
        raise NarrationCoverageError(
            f"UTR extraction matched {matched} rows but only {expected_with_utr} carry "
            f"a UTR -- the pattern is matching something it should not."
        )
    return out
