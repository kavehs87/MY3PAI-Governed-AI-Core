## MY3PAI Governed AI Core — v0.2.0-beta

Deterministic, out-of-band runtime and policy gateway for rights-aware AI content workflows, cryptographic provenance tracking, and reproducible contribution accounting.

### Key Capabilities Included
* **Four-State Policy Gateway (`src/policy/`):** Out-of-band evaluation decoupling agents from authorization logic (`Allowed`, `Conditional`, `Human Review`, `Blocked`).
* **Deterministic Workflow Engine (`src/workflows/`):** Hash-chained audit traces with monotonic timestamping.
* **Instant Revocation Quarantine (`src/connectors/`):** Sub-millisecond synchronous connector isolation and token invalidation on asset withdrawal.
* **Fixed-Point Financial Ledger (`src/accounting/`):** Zero floating-point arithmetic; exact integer basis points (`ROUND_HALF_EVEN`) guaranteeing byte-level allocation reproducibility.
* **Pre-Ingestion Admission Pipeline (`src/training/`):** Opt-out reservation verification and Merkle-pinned corpus build manifests.

### Verification & Performance Baselines
* **Acceptance Gates:** 60 deterministic tests passing in `< 2s` (`make verify`).
* **Stress Profile:** k6 load testing sustained 1,000 RPS with policy evaluation p95 latency at `0.50ms` (`benchmarks/k6_gateway_stress.js`).
* **Revocation Propagation:** Cutoff latency measured at `0.03ms` on withdrawal trigger.
