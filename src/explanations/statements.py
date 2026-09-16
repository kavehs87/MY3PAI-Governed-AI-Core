"""Human-readable statements rendered from integer ledger state.

Formatting divides cents for display only; no float enters the accounting
path. Statements are snapshots, not records of authority: the ledger plus
raw execution traces remain the source of truth for replay.
"""

from __future__ import annotations

from src.accounting.ledger import ContributionLedger


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    return f"{sign}{(abs_cents // 100)}.{abs_cents % 100:02d}"


def custodian_statement_text(ledger: ContributionLedger, custodian_id: str) -> str:
    summary = ledger.statement_for_custodian(custodian_id)
    lines = [
        f"Custodian statement: {custodian_id}",
        f"Gross allocated: {format_cents(summary['gross_cents'])}",
        f"Adjustments: {format_cents(summary['adjustments_cents'])}",
        f"Net payable: {format_cents(summary['net_cents'])}",
    ]
    return "\n".join(lines)


def run_statement_text(
    ledger: ContributionLedger,
    run_id: str,
    revenue_cents: int,
) -> str:
    records = ledger.records_for_run(run_id)
    lines = [f"Run statement: {run_id} (revenue {format_cents(revenue_cents)})"]
    for record in records:
        net = ledger.net_for_record(record.record_id)
        lines.append(
            f"  {record.asset_id} -> {record.custodian_id}: "
            f"alloc {format_cents(record.allocated_cents)} "
            f"({record.share_basis_points}bp) net {format_cents(net)}"
        )
    return "\n".join(lines)
