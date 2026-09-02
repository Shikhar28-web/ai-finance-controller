#!/usr/bin/env python3
"""Render the README constants table from afc/config.py -- one source, no drift.

The owner's condition on SPEC 7: every constant appears in the README with its
value and a one-line justification. Hand-maintaining that table guarantees it goes
stale, so it is generated between markers and CI fails if the file is out of sync.

Run: python tools/render_constants.py [--check]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from afc import config  # noqa: E402

README = ROOT / "README.md"
BEGIN = "<!-- BEGIN GENERATED CONSTANTS -->"
END = "<!-- END GENERATED CONSTANTS -->"


def format_value(name: str, value: object) -> str:
    if name.endswith("_PAISE"):
        return f"`{value:,}` ({config_money(value)})"
    if name.endswith("_BPS"):
        return f"`{value:,}` ({value / 100:g}%)"  # display only
    if isinstance(value, dict):
        return f"`all {sorted(set(value.values()))[0]}`"
    return f"`{value}`"


def config_money(paise: object) -> str:
    whole, frac = divmod(int(paise), 100)  # type: ignore[arg-type]
    return f"Rs {whole:,}.{frac:02d}"


def render() -> str:
    rows = ["| Constant | Value | Why this value |", "|---|---|---|"]
    for name in sorted(config.JUSTIFICATIONS):
        value = getattr(config, name)
        why = " ".join(config.JUSTIFICATIONS[name].split())
        rows.append(f"| `{name}` | {format_value(name, value)} | {why} |")
    return "\n".join(rows)


def main() -> int:
    table = f"{BEGIN}\n\n{render()}\n\n{END}"
    text = README.read_text()
    if BEGIN not in text or END not in text:
        print(f"render_constants: markers missing from {README}", file=sys.stderr)
        return 1
    head, _, rest = text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    updated = head + table + tail
    if "--check" in sys.argv:
        if updated != text:
            print("render_constants: README constants table is stale -- "
                  "run `make readme`", file=sys.stderr)
            return 1
        print("render_constants: README constants table is in sync")
        return 0
    README.write_text(updated)
    print(f"render_constants: wrote {len(config.JUSTIFICATIONS)} constants into README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
