"""The firewall must actually bite. SPEC 3, SPEC 12.

Each test writes a deliberately violating module into a real guarded package,
runs the real linter over it, and asserts the violation is caught -- then removes
it. Testing against the real tree rather than a synthetic one means the test
cannot drift away from what `make check` does in CI.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.check_imports import collect  # noqa: E402


@contextmanager
def violating_module(package: str, source: str) -> Iterator[Path]:
    path = ROOT / package.replace(".", "/") / "_fw_probe.py"
    path.write_text(source)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def rules_for(package: str, source: str) -> list[str]:
    with violating_module(package, source) as path:
        return [v.rule for v in collect() if v.path == path]


def test_clean_tree_has_no_violations():
    assert collect() == []


def test_core_may_not_import_the_llm_package():
    assert "import-layering" in rules_for("afc.core", "from afc.llm.narrate import narrate\n")


def test_metrics_may_not_import_the_llm_package():
    assert "import-layering" in rules_for("afc.metrics", "import afc.llm\n")


@pytest.mark.parametrize("package", ["afc.core", "afc.metrics", "afc.generate", "afc.ingest"])
def test_vendor_sdk_is_contained_to_the_client(package: str):
    assert "sdk-containment" in rules_for(package, "import anthropic\n")


def test_llm_client_is_the_one_place_the_sdk_may_be_imported():
    # afc.llm is not a guarded package, and client is the designated SDK holder.
    path = ROOT / "afc/llm/client.py"
    existed = path.exists()
    original = path.read_text() if existed else None
    path.write_text("import anthropic\n\n_ = anthropic\n")
    try:
        assert [v for v in collect() if v.path == path] == []
    finally:
        if existed:
            path.write_text(original)  # type: ignore[arg-type]
        else:
            path.unlink()


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\n\nx = datetime.now()\n",
        "import datetime\n\nx = datetime.date.today()\n",
        "import time\n\nx = time.time()\n",
        "from time import time\n",
        "import uuid\n\nx = uuid.uuid4()\n",
    ],
)
def test_wall_clock_is_banned_in_core(source: str):
    # AWAITING_BANK vs UNRESOLVED turns on T+2; a clock read would reclassify a
    # frozen dataset overnight and make the held-out matrix unreproducible.
    assert "clock-ban" in rules_for("afc.core", source)


def test_global_rng_is_banned_in_the_generator():
    assert "clock-ban" in rules_for("afc.generate", "import random\n\nx = random.random()\n")


def test_injected_seeded_rng_is_allowed_in_the_generator():
    source = "import random\n\nrng = random.Random(42)\nx = rng.random()\n"
    assert rules_for("afc.generate", source) == []


def test_core_may_not_touch_storage_but_ingest_may():
    assert "import-layering" in rules_for("afc.core", "import sqlite3\n")
    assert rules_for("afc.ingest", "import csv\n") == []


def test_core_may_not_import_orchestration():
    assert "import-layering" in rules_for("afc.core", "from afc.pipeline import run\n")
