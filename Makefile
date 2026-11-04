.PHONY: setup doctor spike test test-live lint
setup:
	uv sync --locked
doctor:
	uv run autoqa doctor
spike:
	uv run python scripts/run_m0_spike.py
test:
	uv run --locked pytest -q
test-live:
	uv run --locked pytest -q -m live_api
lint:
	uv run --locked ruff check autoqa scripts tests
	uv run --locked ruff format --check autoqa scripts tests
