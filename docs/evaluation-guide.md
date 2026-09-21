# Evaluator walkthrough

For technical evaluation committees and auditors. Total hands-on time: under 15 minutes.

## 1. Fast gate (1 minute)

```bash
git clone https://github.com/kavehs87/MY3PAI-Governed-AI-Core.git
cd MY3PAI-Governed-AI-Core
make verify
```

Runs 60 seed-fixed tests (acceptance gates, ledger edges, schema contracts,
service wire path). No network, no fuzz. Expect `60 passed`.

Hermetic equivalent (pinned Python 3.12 image, no host toolchain):

```bash
docker compose up --build --exit-code-from test-runner
```

Expect `test-runner-1 exited with code 0`.

## 2. Full suite (2 minutes)

```bash
make install
make test
```

75 tests including property fuzz (`hypothesis`) and a 10,000-record ledger
replay with byte-identical reconciliation. Coverage gate: >90% (measured 95%).

## 3. Adversarial policy gates

```bash
make test-gates
```

29 cases in `tests/test_policy_gates.py`: expired rights, cross-tenant leaks,
mandate tampering, privilege escalation, prompt-injection phrasings,
token tampering/expiry/replay, withdrawal races. Zero false positives on
`Blocked` expectations; benign traffic asserts `Allowed`.

## 4. Load and revocation benchmarks

Requires `k6`:

```bash
make benchmark
```

Boots `src/policy/service.py`, runs `benchmarks/k6_gateway_stress.js`
(200/500/1000 RPS evaluation plus 8 revocation iterations), writes
`evidence/benchmarks/k6_results.json`. See `benchmark-method.md` for method.

## 5. Evidence inspection

```bash
make evidence
cat evidence/claims.csv
ls evidence/runs/<run-id>/
```

Each claim row names its verifier (`verified_by`) and artifact. Cross-check a
decision in `decisions.json` against the gateway rule order in
`src/policy/rules.py`, and a ledger statement in `statement.txt` against
`ContributionLedger.replay_run`.
