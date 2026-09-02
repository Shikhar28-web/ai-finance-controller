#!/usr/bin/env python3
"""The LLM firewall and the clock ban, enforced structurally (SPEC 3, SPEC 12).

SPEC 3 requires that no LLM output reach any classification, scoring, matching or
arithmetic path, and that metrics be unchanged if every LLM call returned garbage.
A single behavioural test proves that for one code path on one day; this linter
makes the violation unrepresentable, by walking the AST of every module under afc/
and failing the build if a guarded package can even *reach* the LLM.

Two mechanisms live here:

  1. IMPORT LAYERING  -- afc.core / afc.metrics / afc.generate / afc.ingest may not
     import afc.llm, any vendor SDK, or any network/storage module. Only
     afc.llm.client may import the vendor SDK. Because the LLM's only output type
     (AiNote) is defined inside afc.llm, no guarded module can even name it.

  2. CLOCK & ENTROPY BAN -- the same packages may not call datetime.now(),
     date.today(), time.time(), uuid4(), os.urandom(), or module-level random.*.
     AWAITING_BANK vs UNRESOLVED turns on a T+2 window, so a wall-clock read would
     silently reclassify a frozen dataset overnight and make the sealed_eval confusion
     matrix unreproducible. Randomness must come from an injected random.Random(seed).

Run: python tools/check_imports.py     (wired into `make check` and CI)
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "afc"

LLM_PACKAGE = "afc.llm"

VENDOR_SDKS = frozenset({"anthropic", "openai", "cohere", "google", "litellm"})
NETWORK_MODULES = frozenset(
    {"httpx", "requests", "aiohttp", "urllib", "urllib3", "http", "socket", "ftplib"}
)
STORAGE_MODULES = frozenset({"sqlite3", "shelve", "dbm"})

# Attribute calls banned outright in guarded packages, as dotted suffixes.
BANNED_CALLS = {
    "datetime.now": "wall clock",
    "datetime.utcnow": "wall clock",
    "date.today": "wall clock",
    "time.time": "wall clock",
    "time.monotonic": "wall clock",
    "time.perf_counter": "wall clock",
    "uuid.uuid4": "nondeterministic id",
    "os.urandom": "nondeterministic entropy",
}
# Silent single-row selection. SPEC 5 calls Level 1 and Level 3 1:1 joins, but
# duplicate_ledger_entry and duplicate_bank_credit make them 1:2 -- taking one row
# dedups by accident and the injected fault is never detected. Group and assert
# cardinality instead; Level 0 owns the duplicate finding.
BANNED_SELECTORS = {
    "first": "takes one row from a group that may legitimately hold two",
    "one_or_none": "same: it hides the duplicate rather than reporting it",
    "find_one": "same",
}
# `from X import Y` forms that smuggle the same thing past the dotted check.
BANNED_FROM_IMPORTS = {
    ("time", "time"), ("time", "monotonic"), ("time", "perf_counter"),
    ("uuid", "uuid4"), ("os", "urandom"),
}
# random.* module-level functions use the global RNG. Only random.Random(seed) is ok.
RANDOM_ALLOWED_ATTRS = frozenset({"Random", "SystemRandom"})


@dataclass(frozen=True)
class Rule:
    forbidden_packages: frozenset[str]
    forbidden_modules: frozenset[str]
    ban_clock: bool


PURE = Rule(
    forbidden_packages=frozenset({LLM_PACKAGE, "afc.db", "afc.pipeline", "afc.cli"}),
    forbidden_modules=VENDOR_SDKS | NETWORK_MODULES | STORAGE_MODULES,
    ban_clock=True,
)
LOADER = Rule(
    forbidden_packages=frozenset({LLM_PACKAGE, "afc.cli"}),
    forbidden_modules=VENDOR_SDKS | NETWORK_MODULES,
    ban_clock=True,
)

# Longest prefix wins.
GUARDED: dict[str, Rule] = {
    "afc.core": PURE,
    "afc.metrics": PURE,
    "afc.generate": LOADER,
    "afc.ingest": LOADER,
}

# Only this module may import a vendor SDK.
SDK_ALLOWED = "afc.llm.client"


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    rule: str
    detail: str

    def render(self) -> str:
        rel = self.path.relative_to(ROOT)
        return f"  {rel}:{self.line}\n      [{self.rule}] {self.detail}"


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def rule_for(mod: str) -> tuple[str, Rule] | None:
    best: tuple[str, Rule] | None = None
    for prefix, rule in GUARDED.items():
        if (mod == prefix or mod.startswith(prefix + ".")) and (
            best is None or len(prefix) > len(best[0])
        ):
            best = (prefix, rule)
    return best


def dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def imported_targets(node: ast.Import | ast.ImportFrom, mod: str) -> list[str]:
    """Absolute module paths this statement pulls in."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if node.level:  # relative import -> resolve against the current package
        base = mod.rsplit(".", node.level)[0] if "." in mod else ""
        return [f"{base}.{node.module}" if node.module else base]
    return [node.module] if node.module else []


def check_file(path: Path) -> list[Violation]:
    mod = module_name(path)
    found = rule_for(mod)
    tree = ast.parse(path.read_text(), filename=str(path))
    out: list[Violation] = []

    # SDK containment applies to every module, guarded or not.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for target in imported_targets(node, mod):
                root = target.split(".")[0]
                if root in VENDOR_SDKS and mod != SDK_ALLOWED:
                    out.append(
                        Violation(path, node.lineno, "sdk-containment",
                                  f"only {SDK_ALLOWED} may import the vendor SDK "
                                  f"'{target}'; {mod} may not")
                    )
    if found is None:
        return out
    prefix, rule = found

    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for target in imported_targets(node, mod):
                root = target.split(".")[0]
                for bad in rule.forbidden_packages:
                    if target == bad or target.startswith(bad + "."):
                        why = ("the LLM firewall: guarded code must not be able to "
                               "reach LLM output") if bad == LLM_PACKAGE else \
                              "guarded code must stay pure (no I/O, no orchestration)"
                        out.append(
                            Violation(path, node.lineno, "import-layering",
                                      f"{prefix} may not import '{target}' -- {why}")
                        )
                if root in rule.forbidden_modules:
                    out.append(
                        Violation(path, node.lineno, "import-layering",
                                  f"{prefix} may not import '{target}'")
                    )
            if rule.ban_clock and isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    if (node.module, alias.name) in BANNED_FROM_IMPORTS:
                        out.append(
                            Violation(path, node.lineno, "clock-ban",
                                      f"'from {node.module} import {alias.name}' is "
                                      f"banned in {prefix}")
                        )
                    if node.module == "random" and alias.name not in RANDOM_ALLOWED_ATTRS:
                        out.append(
                            Violation(path, node.lineno, "clock-ban",
                                      f"'from random import {alias.name}' uses the "
                                      f"global RNG; inject random.Random(seed)")
                        )

        if rule.ban_clock and isinstance(node, ast.Call):
            name = dotted(node.func)
            if not name:
                continue
            for banned, why in BANNED_CALLS.items():
                if name == banned or name.endswith("." + banned):
                    out.append(
                        Violation(path, node.lineno, "clock-ban",
                                  f"{name}() is banned in {prefix} ({why}); "
                                  f"read as_of_date from the run manifest instead")
                    )
            _, _, leaf = name.rpartition(".")
            if leaf in BANNED_SELECTORS and "." in name:
                out.append(
                    Violation(path, node.lineno, "no-silent-dedup",
                              f"{name}() is banned in {prefix}: "
                              f"{BANNED_SELECTORS[leaf]}. Group and assert cardinality "
                              f"-- afc.core.integrity owns duplicate findings.")
                )
            head, _, attr = name.rpartition(".")
            if head.split(".")[-1] == "random" and attr not in RANDOM_ALLOWED_ATTRS:
                out.append(
                    Violation(path, node.lineno, "clock-ban",
                              f"{name}() uses the global RNG and is not reproducible; "
                              f"inject random.Random(seed)")
                )
    return out


def collect(root: Path = PKG) -> list[Violation]:
    out: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        out.extend(check_file(path))
    return out


def main() -> int:
    if not PKG.exists():
        print(f"firewall: no package at {PKG}", file=sys.stderr)
        return 1
    violations = collect()
    scanned = len(list(PKG.rglob("*.py")))
    if not violations:
        print(f"firewall: OK -- {scanned} modules scanned, "
              f"{len(GUARDED)} guarded packages, 0 violations")
        return 0
    print(f"firewall: FAILED -- {len(violations)} violation(s) "
          f"across {scanned} scanned modules\n", file=sys.stderr)
    for v in violations:
        print(v.render(), file=sys.stderr)
    notes = {
        "import-layering": "SPEC 3: no LLM output may be read by any classification, "
                           "scoring, matching or arithmetic code path.",
        "sdk-containment": "SPEC 3: the vendor SDK lives behind afc.llm.client alone.",
        "clock-ban": "SPEC 12: fixed seeds and a frozen as_of; no wall clock in a "
                     "classification path.",
        "no-silent-dedup": "SPEC 5: duplicates are found by grouping and asserting "
                           "cardinality, never by taking one row.",
    }
    for rule in sorted({v.rule for v in violations}):
        if rule in notes:
            print(f"\n{notes[rule]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
