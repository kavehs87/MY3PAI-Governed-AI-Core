"""Deterministic accounting replay over 10,000 synthetic usage records."""

from __future__ import annotations

import hashlib
import random

from src.accounting.ledger import ContributionLedger


def test_accounting_replay_10k_zero_drift() -> None:
    rng = random.Random(20260210)
    ledger = ContributionLedger()
    runs: list[tuple[str, int, list[tuple[str, str, int]]]] = []
    n_assets = 6
    for run_idx in range(10_000):
        run_id = f"synth-{run_idx:05d}"
        revenue = rng.choice([0, 1, 7, 99, 100, 101, 12_345, 1_000_000])
        cuts = sorted(rng.sample(range(1, 10_000), n_assets - 1))
        weights: list[int] = []
        prev = 0
        for cut in cuts:
            weights.append(cut - prev)
            prev = cut
        weights.append(10_000 - prev)
        rng.shuffle(weights)
        contributions = [(f"asset-{i}", f"custodian-{i % 3}", w) for i, w in enumerate(weights)]
        ledger.post_run(run_id, revenue, contributions)
        runs.append((run_id, revenue, contributions))

    assert ledger.record_count() == 10_000 * n_assets

    for run_id, revenue, contributions in runs:
        replayed = ledger.replay_run(run_id, revenue, contributions)
        stored = ledger.records_for_run(run_id)
        assert [r.model_dump_json() for r in replayed] == [r.model_dump_json() for r in stored]
        assert sum(r.allocated_cents for r in stored) == revenue


def test_replay_uses_stable_content_hash() -> None:
    ledger = ContributionLedger()
    contributions = [("a", "c-a", 5000), ("b", "c-b", 5000)]
    ledger.post_run("run-x", 101, contributions)
    stored = ledger.records_for_run("run-x")
    fingerprint = hashlib.sha256("".join(r.model_dump_json() for r in stored).encode()).hexdigest()
    replayed = ledger.replay_run("run-x", 101, contributions)
    refingerprint = hashlib.sha256(
        "".join(r.model_dump_json() for r in replayed).encode()
    ).hexdigest()
    assert fingerprint == refingerprint
