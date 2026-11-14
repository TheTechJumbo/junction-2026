PYTHON ?= python3
VENV := .venv/bin

.PHONY: setup test lint check schemas
setup:
	$(PYTHON) -m venv .venv
	$(VENV)/python -m pip install -r requirements-dev.lock
	$(VENV)/python -m pip install --no-deps --no-build-isolation -e .
test:
	$(VENV)/python -m pytest -q -m 'not live and not integration'
lint:
	$(VENV)/ruff check contracts backend tests
schemas:
	$(VENV)/python -m contracts.export
check: lint test
	$(VENV)/python -m contracts.export --check

.PHONY: run dashboard-check test-http rehearse
run:
	$(VENV)/uvicorn backend.run:create_application --factory --host 127.0.0.1 --port 8000
dashboard-check:
	npm --prefix dashboard run check
test-http:
	$(VENV)/python -m pytest -q tests/integration -m integration
rehearse:
	$(VENV)/python scripts/rehearse.py
