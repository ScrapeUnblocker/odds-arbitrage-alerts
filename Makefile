.PHONY: install lint format test run dry-run status clean

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest

run:
	odds-arbitrage-alerts run

dry-run:
	odds-arbitrage-alerts run --dry-run

status:
	odds-arbitrage-alerts status

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
