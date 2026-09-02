# `make metrics` is the demo path and needs nothing but Python 3.12 -- the runtime has
# no third-party dependencies. The venv is only for the dev tooling (pytest, ruff), so
# PY falls back to system python3 when it is absent. A fresh clone must not need setup
# to produce a number.
PY := $(shell [ -x ./.venv/bin/python ] && echo ./.venv/bin/python || command -v python3)
RUFF := ./.venv/bin/ruff

.PHONY: metrics setup check lint firewall readme readme-write census test

metrics:
	$(PY) tools/run_metrics.py dev --json metrics_summary.json

setup:
	python3 -m venv .venv
	./.venv/bin/python -m pip install -q --upgrade pip
	./.venv/bin/python -m pip install -q pytest ruff

check: lint firewall readme census test

lint:
	$(RUFF) check afc tools tests

firewall:
	$(PY) tools/check_imports.py

readme:
	$(PY) tools/render_constants.py --check

readme-write:
	$(PY) tools/render_constants.py

census:
	$(PY) tools/census.py

test:
	$(PY) -m pytest
