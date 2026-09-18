"""Edge cases for integer ledger math."""

from __future__ import annotations

import pytest

from src.accounting.ledger import (
    ContributionLedger,
    LedgerError,
    allocate_cents,
    round_half_even_cents,
)


def test_zero_revenue_run_allocates_zeros() -> None:
    ledger = ContributionLedger()
    records = ledger.post_run("zero", 0, [("a", "c", 6000), ("b", "c", 4000)])
    assert [r.allocated_cents for r in records] == [0, 0]


def test_float_revenue_rejected() -> None:
    ledger = ContributionLedger()
    with pytest.raises(LedgerError):
        ledger.post_run("bad", 10.5, [("a", "c", 10_000)])  # type: ignore[arg-type]


def test_weights_must_sum_to_10000() -> None:
    with pytest.raises(LedgerError):
        allocate_cents(100, [5000, 4999])


def test_largest_remainder_exact_split() -> None:
    assert allocate_cents(100, [3333, 3333, 3334]) == [33, 33, 34]
    assert sum(allocate_cents(101, [5000, 5000])) == 101


def test_micro_cent_tie_breaks_to_lowest_index() -> None:
    assert allocate_cents(1, [5000, 5000]) == [1, 0]


def test_round_half_even_bankers_cases() -> None:
    assert round_half_even_cents(1, 1, 2) == 0
    assert round_half_even_cents(3, 1, 2) == 2
    assert round_half_even_cents(5, 1, 2) == 2
    assert round_half_even_cents(7, 1, 2) == 4


def test_dispute_offset_and_partial_refund() -> None:
    ledger = ContributionLedger()
    (record,) = ledger.post_run("r1", 1000, [("a", "cust-1", 10_000)])
    assert record.allocated_cents == 1000
    ledger.apply_dispute_offset("off-1", record.record_id, -250, "duplicate count")
    assert ledger.net_for_record(record.record_id) == 750
    ledger.apply_partial_refund(record.record_id, 1, 2)
    refund = round_half_even_cents(1000, 1, 2)
    assert ledger.net_for_record(record.record_id) == 750 - refund


def test_statement_aggregates() -> None:
    ledger = ContributionLedger()
    ledger.post_run("r2", 300, [("a", "cust-9", 5000), ("b", "cust-9", 5000)])
    summary = ledger.statement_for_custodian("cust-9")
    assert summary["gross_cents"] == 300
    assert summary["net_cents"] == 300
