.PHONY: install test verify benchmark lint typecheck evidence serve clean

PY ?= python3
K6_URL ?= http://127.0.0.1:8410

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	ruff check schemas src tests scripts
	ruff format --check schemas src tests scripts

typecheck:
	mypy schemas src tests

test:
	$(PY) -m pytest

# Deterministic acceptance gate: policy gates, ledger edges, schema
# contracts, and service wire path. Bounded, seed-fixed, no fuzz, no
# network. Completes in seconds; full suite remains under `make test`.
verify:
	$(PY) -m pytest tests/test_policy_gates.py tests/test_ledger_edges.py tests/test_schemas.py tests/test_coverage_extra.py -q -o addopts=""

test-gates:
	$(PY) -m pytest tests/test_policy_gates.py -v

test-replay:
	$(PY) -m pytest tests/test_accounting_replay.py -v

coverage:
	$(PY) -m pytest --cov=schemas --cov=src --cov-report=term-missing --cov-fail-under=90

serve:
	$(PY) -m src.policy.service --host 127.0.0.1 --port 8410

benchmark: evidence/benchmarks/k6_results.json

evidence/benchmarks/k6_results.json: benchmarks/k6_gateway_stress.js
	@echo "Starting gateway on $(K6_URL) for k6 run..."
	@$(PY) scripts/run_benchmark.py --out evidence/benchmarks/k6_results.json

evidence:
	$(PY) scripts/generate_evidence.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
