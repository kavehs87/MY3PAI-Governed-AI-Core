# Benchmark method

## Harness

- Target: `src/policy/service.py` (stdlib `http.server` + 32-worker pool),
  booted on `127.0.0.1:8410` by `scripts/run_benchmark.py`.
- Load generator: `k6` executing `benchmarks/k6_gateway_stress.js`.
- Scenarios: constant-arrival-rate evaluation at 200 RPS/20s, 500 RPS/20s,
  1000 RPS/15s (tagged sub-metrics with per-level `p(95)<12ms` thresholds),
  then 8 shared-iteration revocations asserting immediate block and
  `revoke_to_block_ms < 50` (the service measures its own revoke-to-probe
  latency server-side per iteration).
- Results: `evidence/benchmarks/k6_results.json` (sub-metric p50/p95/p99,
  counts, failure rate, threshold verdict); raw summary retained at
  `evidence/benchmarks/k6_summary.json`.

## Recorded run

2026-09-21T11:00:17Z, localhost, 29,011 requests, failure rate 0.0,
thresholds passed. Check detail: `status 200` 29,011/29,011;
`state Allowed` 29,002/29,003 (one evaluation landed in the revocation
window and was correctly `Blocked`); `blocked immediately` 8/8;
`revoke-to-block < 50ms` 8/8.

## Threats to validity

- Localhost loopback: figures exclude real network latency; they measure
  gateway overhead only, which is the claim being tested.
- Shared runner hardware: CI machines vary; the suite asserts thresholds
  rather than exact values, with 20x headroom observed (p95 ≈ 0.5ms
  against a 12ms budget).
- Revocation sample size is 8 iterations by design (each revocation is
  state-changing); the in-process bound is additionally covered by
  `tests/test_runtime.py::test_withdrawal_propagation_bound` (< 50ms)
  and the 32-worker concurrency test.
