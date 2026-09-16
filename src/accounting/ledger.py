"""Deterministic integer ledger for contribution accounting.

All math is integer minor units (cents) and integer basis points. Allocation
of revenue_cents across weighted shares uses exact integer division with
largest-remainder distribution; ties break toward the lowest asset_id so
replays are byte-identical. Rounding mode is ROUND_HALF_EVEN at the single
point where a non-integer intermediate can arise (basis-point weighting
reduces to integer floor + remainder, which is exact, not rounded).

Reconciliation: recompute(record_inputs) reproduces allocated_cents and
reconciliation_hash bit-for-bit from raw inputs, enabling independent replay.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal

from schemas.ledger import BASIS_POINTS_TOTAL, DisputeOffset, LedgerRecord


class LedgerError(Exception):
    pass


def _reject_non_integer_money(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise LedgerError(f"{name} must be an integer, not bool")
    if isinstance(value, float):
        raise LedgerError(f"{name} must be integer minor units, not float")
    if isinstance(value, Decimal):
        raise LedgerError(f"{name} must be integer minor units, not Decimal")
    if not isinstance(value, int):
        raise LedgerError(f"{name} must be an integer")
    return value


def allocate_cents(revenue_cents: int, weights_bp: list[int]) -> list[int]:
    """Split revenue_cents across weights using largest-remainder.

    Exact: floors first, then distribute the leftover cents (always fewer
    than len(weights)) to the largest fractional remainders. Ties resolve by
    lowest index, making output deterministic across replays.
    """
    revenue_cents = _reject_non_integer_money(revenue_cents, "revenue_cents")
    if revenue_cents < 0:
        raise LedgerError("revenue_cents must be >= 0")
    if not weights_bp:
        raise LedgerError("weights must be non-empty")
    total = 0
    for w in weights_bp:
        if not isinstance(w, int) or isinstance(w, bool) or w < 0:
            raise LedgerError("weights must be non-negative integers")
        total += w
    if total != BASIS_POINTS_TOTAL:
        raise LedgerError(f"weights must sum to {BASIS_POINTS_TOTAL}, got {total}")
    if revenue_cents == 0:
        return [0] * len(weights_bp)
    floors: list[int] = []
    remainders: list[int] = []
    for w in weights_bp:
        product = revenue_cents * w
        floors.append(product // BASIS_POINTS_TOTAL)
        remainders.append(product % BASIS_POINTS_TOTAL)
    leftover = revenue_cents - sum(floors)
    order = sorted(range(len(weights_bp)), key=lambda i: (-remainders[i], i))
    result = list(floors)
    for k in range(leftover):
        result[order[k % len(order)]] += 1
    return result


def round_half_even_cents(amount_cents: int, ratio_numer: int, ratio_denom: int) -> int:
    """Banker's rounding for a single ratio application, integer in/out.

    Computes amount_cents * ratio_numer / ratio_denom with ROUND_HALF_EVEN on
    the exact rational result. Used for partial refunds where the refund ratio
    is not representable in basis points.
    """
    for name, v in (("amount_cents", amount_cents), ("numer", ratio_numer), ("denom", ratio_denom)):
        if not isinstance(v, int) or isinstance(v, bool):
            raise LedgerError(f"{name} must be an integer")
    if ratio_denom <= 0:
        raise LedgerError("ratio denominator must be positive")
    if amount_cents < 0 or ratio_numer < 0:
        raise LedgerError("amount and numerator must be non-negative")
    quotient, remainder = divmod(amount_cents * ratio_numer, ratio_denom)
    twice = remainder * 2
    if twice < ratio_denom:
        return quotient
    if twice > ratio_denom:
        return quotient + 1
    return quotient if quotient % 2 == 0 else quotient + 1


def reconciliation_hash(
    run_id: str, asset_id: str, revenue_cents: int, share_bp: int, allocated_cents: int
) -> str:
    canonical = json.dumps(
        {
            "run_id": run_id,
            "asset_id": asset_id,
            "revenue_cents": revenue_cents,
            "share_bp": share_bp,
            "allocated_cents": allocated_cents,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContributionLedger:
    """Append-only ledger keyed by record_id with replay support."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, LedgerRecord] = {}
        self._offsets: dict[str, list[DisputeOffset]] = {}

    def post_run(
        self,
        run_id: str,
        revenue_cents: int,
        contributions: list[tuple[str, str, int]],
        created_at: datetime | None = None,
    ) -> list[LedgerRecord]:
        """Post one revenue event split across (asset_id, custodian_id, weight_bp)."""
        revenue_cents = _reject_non_integer_money(revenue_cents, "revenue_cents")
        if not contributions:
            raise LedgerError("contributions must be non-empty")
        weights = [w for _, _, w in contributions]
        shares = allocate_cents(revenue_cents, weights)
        now = created_at or datetime.now(timezone.utc)
        records: list[LedgerRecord] = []
        with self._lock:
            for idx, ((asset_id, custodian_id, weight), allocated) in enumerate(
                zip(contributions, shares, strict=True)
            ):
                record_id = f"{run_id}:{idx:04d}"
                if record_id in self._records:
                    raise LedgerError(f"duplicate record_id {record_id!r}")
                record = LedgerRecord(
                    record_id=record_id,
                    run_id=run_id,
                    asset_id=asset_id,
                    custodian_id=custodian_id,
                    revenue_cents=revenue_cents,
                    share_basis_points=weight,
                    allocated_cents=allocated,
                    reconciliation_hash=reconciliation_hash(
                        run_id, asset_id, revenue_cents, weight, allocated
                    ),
                    created_at=now,
                )
                self._records[record_id] = record
                records.append(record)
        return records

    def apply_dispute_offset(
        self,
        offset_id: str,
        record_id: str,
        adjustment_cents: int,
        reason: str,
        created_at: datetime | None = None,
    ) -> DisputeOffset:
        if not isinstance(adjustment_cents, int) or isinstance(adjustment_cents, bool):
            raise LedgerError("adjustment_cents must be an integer")
        with self._lock:
            if record_id not in self._records:
                raise LedgerError(f"unknown record_id {record_id!r}")
            offset = DisputeOffset(
                offset_id=offset_id,
                record_id=record_id,
                adjustment_cents=adjustment_cents,
                reason=reason,
                created_at=created_at or datetime.now(timezone.utc),
            )
            self._offsets.setdefault(record_id, []).append(offset)
            return offset

    def apply_partial_refund(self, record_id: str, numer: int, denom: int) -> DisputeOffset:
        with self._lock:
            record = self._records.get(record_id)
            if record is None:
                raise LedgerError(f"unknown record_id {record_id!r}")
            refund = round_half_even_cents(record.allocated_cents, numer, denom)
            return self.apply_dispute_offset(
                offset_id=f"refund:{record_id}:{numer}/{denom}",
                record_id=record_id,
                adjustment_cents=-refund,
                reason=f"partial refund {numer}/{denom}",
            )

    def net_for_record(self, record_id: str) -> int:
        with self._lock:
            record = self._records[record_id]
            total = record.allocated_cents
            for offset in self._offsets.get(record_id, []):
                total += offset.adjustment_cents
            return total

    def records_for_run(self, run_id: str) -> list[LedgerRecord]:
        with self._lock:
            return sorted(
                (r for r in self._records.values() if r.run_id == run_id),
                key=lambda r: r.record_id,
            )

    def replay_run(
        self, run_id: str, revenue_cents: int, contributions: list[tuple[str, str, int]]
    ) -> list[LedgerRecord]:
        """Independently recompute a run's records and compare byte-for-byte."""
        expected = self.records_for_run(run_id)
        weights = [w for _, _, w in contributions]
        shares = allocate_cents(revenue_cents, weights)
        replayed: list[LedgerRecord] = []
        for idx, ((asset_id, custodian_id, weight), allocated) in enumerate(
            zip(contributions, shares, strict=True)
        ):
            replayed.append(
                LedgerRecord(
                    record_id=f"{run_id}:{idx:04d}",
                    run_id=run_id,
                    asset_id=asset_id,
                    custodian_id=custodian_id,
                    revenue_cents=revenue_cents,
                    share_basis_points=weight,
                    allocated_cents=allocated,
                    reconciliation_hash=reconciliation_hash(
                        run_id, asset_id, revenue_cents, weight, allocated
                    ),
                    created_at=expected[idx].created_at,
                )
            )
        for got, want in zip(replayed, expected, strict=True):
            if got.model_dump_json() != want.model_dump_json():
                raise LedgerError(f"replay drift on {want.record_id}")
        return replayed

    def statement_for_custodian(self, custodian_id: str) -> dict[str, int]:
        with self._lock:
            gross = 0
            adjustments = 0
            for record in self._records.values():
                if record.custodian_id != custodian_id:
                    continue
                gross += record.allocated_cents
                for offset in self._offsets.get(record.record_id, []):
                    adjustments += offset.adjustment_cents
            return {
                "gross_cents": gross,
                "adjustments_cents": adjustments,
                "net_cents": gross + adjustments,
            }

    def record_count(self) -> int:
        with self._lock:
            return len(self._records)
