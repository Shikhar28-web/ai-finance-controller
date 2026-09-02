PY := ./.venv/bin/python
RUFF := ./.venv/bin/ruff

.PHONY: check lint firewall readme readme-write census metrics test

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

metrics:
	$(PY) tools/run_metrics.py dev --json metrics_summary.json

test:
	$(PY) -m pytest
