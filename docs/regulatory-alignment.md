# Regulatory alignment

Mechanism-to-mandate mapping. Each item names the enforcing code and the
evidence that verifies it.

## EU AI Act Art. 14 — Human oversight

High-risk actions (`publish`, `train_export`, `bulk_export`) deterministically
route to `Human Review` with a cryptographically generated `REV-*` ticket
(`src/policy/gateway.py`, `HIGH_RISK_ACTIONS`). Reviewer decisions append to
the tamper-evident `reviewer_trail` (`schemas/policy.py`: `ReviewerEntry`).
Verified by `tests/test_policy_gates.py::test_24_high_risk_publish_human_review`
and `::test_25_high_risk_bulk_export_human_review`; artifacts in
`evidence/runs/<run-id>/decisions.json`.

## EU AI Act Art. 53(1)(c) — GPAI copyright transparency

`src/training/admission.py` validates each asset before ingestion (mandate
signature, non-withdrawn, tenant match, validity window, `train` in usage
constraints) and `freeze()` pins the admitted set in a Merkle-rooted
`build_manifest.json`. Post-freeze substitution changes the root and fails
`AdmissionPipeline.verify_manifest`. Verified by
`tests/test_runtime.py::test_training_admission_and_manifest`; artifacts in
`evidence/runs/<run-id>/build_manifest.json`.

## GDPR Art. 17 — Right to erasure / access revocation

`AssetStore.withdraw` plus `ConnectorRegistry.quarantine` halt downstream
retrieval and generation synchronously, without redeploy or downtime.
Verified by `tests/test_runtime.py::test_withdrawal_propagation_bound` and
`::test_concurrent_withdrawal_blocks_all_workers`; operational timing in
`evidence/runs/<run-id>/events.ndjson` and
`evidence/benchmarks/k6_results.json` (8/8 revokes blocked immediately,
revoke-to-block < 50ms each).
