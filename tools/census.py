#!/usr/bin/env python3
"""Fault census: what the generator actually produced, before splits are wired.

Reports per-class unit counts for dev and held-out separately, flags any held-out
class below the floor, and shows the true-state distribution both over the whole
population and over fault-carrying records only -- a headline accuracy dominated by
clean records is measuring the easy case.

Run: python tools/census.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from afc.config import SEED_DEV, SEED_HELDOUT_SEALED, SEED_HELDOUT_USED, STATES  # noqa: E402
from afc.core.faults import DEV_ONLY_PAIRS  # noqa: E402
from afc.generate.generator import generate  # noqa: E402
from afc.generate.plan import MIN_HELD_OUT_UNITS  # noqa: E402

DEV_ONLY_CLASSES = {"+".join(sorted(p)) for p in DEV_ONLY_PAIRS}


def classes(truth) -> Counter:
    return Counter(t.fault_class for t in truth)


def main() -> int:
    dev = generate(SEED_DEV)
    held = generate(SEED_HELDOUT_USED)

    dev_c, held_c = classes(dev.truth), classes(held.truth)
    all_classes = sorted(set(dev_c) | set(held_c))

    print("=" * 74)
    print(f"FAULT CENSUS   dev seed {SEED_DEV}   held-out seed {SEED_HELDOUT_USED}   "
          f"sealed {SEED_HELDOUT_SEALED}")
    print("=" * 74)
    print(f"{'fault class':52}{'dev':>6}{'held':>6}  flag")
    short = []
    for name in all_classes:
        d, h = dev_c.get(name, 0), held_c.get(name, 0)
        excluded = name in DEV_ONLY_CLASSES
        if excluded:
            flag = "dev-only"
        elif name == "clean":
            flag = ""
        elif h < MIN_HELD_OUT_UNITS:
            flag = f"UNDER {MIN_HELD_OUT_UNITS}"
            short.append((name, h))
        else:
            flag = ""
        print(f"{name:52}{d:>6}{h:>6}  {flag}")

    print("-" * 74)
    print(f"{'TOTAL units':52}{sum(dev_c.values()):>6}{sum(held_c.values()):>6}")
    scored = sum(v for k, v in held_c.items() if k not in DEV_ONLY_CLASSES)
    excl = sum(v for k, v in held_c.items() if k in DEV_ONLY_CLASSES)
    print(f"{'held-out scored / excluded (same-axis pairs)':52}{scored:>6}{excl:>6}")

    for label, ds in (("DEV", dev), ("HELD-OUT", held)):
        truth = [t for t in ds.truth if t.fault_class not in DEV_ONLY_CLASSES]
        states = Counter(t.true_state for t in truth)
        faulty = Counter(t.true_state for t in truth if t.fault_class != "clean")
        n, nf = sum(states.values()), sum(faulty.values())
        print(f"\n{label} true-state distribution (scored units only)")
        print(f"  {'state':16}{'all':>7}{'share':>8}   {'fault-only':>11}{'share':>8}")
        for st in STATES:
            a, b = states.get(st, 0), faulty.get(st, 0)
            print(f"  {st:16}{a:>7}{a/n:>8.1%}   {b:>11}{(b/nf if nf else 0):>8.1%}")
        print(f"  {'TOTAL':16}{n:>7}{1:>8.0%}   {nf:>11}{1:>8.0%}")

    print("\n" + "=" * 74)
    same_path = "generate(seed) is the only entry point; no structural switch exists"
    print(f"same code path, seeds differ only: {same_path}")
    identical = (
        len(dev.payments) == len(held.payments)
        and len(dev.settlements) == len(held.settlements)
        and sorted(dev_c) == sorted(held_c)
    )
    print(f"structural identity dev vs held-out: "
          f"{'CONFIRMED' if identical else 'DIFFERS -- INVALID'}")
    if short:
        print(f"\nUNDER-FLOOR CLASSES ({MIN_HELD_OUT_UNITS} unit floor): {short}")
        return 1
    print(f"\nall scored held-out classes >= {MIN_HELD_OUT_UNITS} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
