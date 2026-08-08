.PHONY: install test lint typecheck format demo demo-cli clean help

help:
	@echo "Targets:"
	@echo "  install      install package + dev dependencies (use a venv first)"
	@echo "  test         pytest"
	@echo "  lint         ruff check"
	@echo "  typecheck    mypy"
	@echo "  format       ruff format"
	@echo "  demo         start the FastAPI UI at http://localhost:8080"
	@echo "  demo-cli     replay the family-rainy-day scenario in the terminal"
	@echo "  clean        remove caches and build artifacts"

# --- Setup ---
install:
	pip install -e ".[dev]"

# --- Quality ---
test:
	pytest tests/ -v

lint:
	ruff check src/ tests/

typecheck:
	mypy src/

format:
	ruff format src/ tests/

# --- Demo ---
PORT ?= 8080
DEMO_CMD ?= python3 -m adaptivetouragent.app --port $(PORT)
SCENARIO ?= family-rainy-day
LOG ?= /tmp/atau-run.jsonl

demo:
	@echo "Starting Adaptive Tour Agent at http://localhost:$(PORT)"
	@echo "Requires OPENAI_API_KEY in environment."
	$(DEMO_CMD)

demo-cli:
	@echo "Replaying scenario: $(SCENARIO)"
	@echo "Log: $(LOG)"
	python3 -m adaptivetouragent.demo --scenario=$(SCENARIO) --log $(LOG)

# --- Cleanup ---
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
