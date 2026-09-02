PY := ./.venv/bin/python
RUFF := ./.venv/bin/ruff

.PHONY: check lint firewall test

check: lint firewall test

lint:
	$(RUFF) check afc tools tests

firewall:
	$(PY) tools/check_imports.py

test:
	$(PY) -m pytest
