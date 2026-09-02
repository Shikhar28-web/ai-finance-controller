PY := ./.venv/bin/python
RUFF := ./.venv/bin/ruff

.PHONY: check lint test

check: lint test

lint:
	$(RUFF) check afc tools tests

test:
	$(PY) -m pytest
