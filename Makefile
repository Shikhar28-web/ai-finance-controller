PY := ./.venv/bin/python
RUFF := ./.venv/bin/ruff

.PHONY: check lint firewall readme readme-write census test

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
