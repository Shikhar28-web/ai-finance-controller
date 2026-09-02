"""The only type LLM output may inhabit. SPEC 3.

AiNote is defined inside afc.llm, which afc.core, afc.metrics, afc.generate and
afc.ingest are forbidden to import (tools/check_imports.py). So no classification,
scoring, matching or arithmetic path can name this type, let alone read one. The
quarantine is a property of the import graph, not of anyone's discipline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AiNote:
    text: str
    ai_generated: bool = True

    def as_dict(self) -> dict[str, object]:
        return {"text": self.text, "ai_generated": self.ai_generated}
