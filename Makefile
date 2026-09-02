PY := ./.venv/bin/python
RUFF := ./.venv/bin/ruff

.PHONY: check lint firewall readme readme-write test

check: lint firewall readme test

lint:
	$(RUFF) check afc tools tests

firewall:
	$(PY) tools/check_imports.py

readme:
	$(PY) tools/render_constants.py --check

readme-write:
	$(PY) tools/render_constants.py

test:
	$(PY) -m pytest
