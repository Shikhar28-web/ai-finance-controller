"""Display-only prose over already-computed evidence. SPEC 3.

The narrator receives a finished metrics dictionary and writes English about it. It
cannot influence that dictionary: it runs after every number is computed, its output
lands in AiNote, and the import linter prevents any guarded module from reading one.

GarbageNarrator exists for the SPEC 3 test. Two differently-seeded garbage narrators
must leave the `metrics` block byte-identical while changing the note -- which is the
only way to show the test is testing something. A firewall test that passes because no
LLM call exists anywhere is vacuous.
"""

from __future__ import annotations

import json
import random
from typing import Protocol

from afc.llm.types import AiNote

ALPHABET = "abcdefghijklmnopqrstuvwxyz ,.;"


class Narrator(Protocol):
    def summarise(self, metrics: dict) -> AiNote: ...


class NullNarrator:
    """Default. No network, no key, no dependency -- the pipeline runs without an LLM."""

    def summarise(self, metrics: dict) -> AiNote:
        c = metrics["confusion"]
        return AiNote(
            f"Classified {c['scored']} reconciliation units; "
            f"{c['correct']} matched the recorded ground-truth state."
        )


class GarbageNarrator:
    """Returns noise. Used to prove no metric depends on LLM output."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def summarise(self, metrics: dict) -> AiNote:
        _ = json.dumps(metrics, sort_keys=True)  # reads the evidence, influences nothing
        n = self._rng.randrange(40, 120)
        return AiNote("".join(self._rng.choice(ALPHABET) for _ in range(n)))
