"""Fixed-point ledger contract.

All monetary values are integer minor units (cents). Shares are integer basis
points (1/100 of one percent, 10000 = 100%). Floats never cross this boundary:
admission validators reject non-integer input before it reaches allocation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

BASIS_POINTS_TOTAL = 10_000


class LedgerRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    run_id: str
    asset_id: str
    custodian_id: str
    revenue_cents: int = Field(ge=0)
    share_basis_points: int = Field(ge=0, le=BASIS_POINTS_TOTAL)
    allocated_cents: int = Field(ge=0)
    reconciliation_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime

    @field_validator("revenue_cents", "allocated_cents", mode="before")
    @classmethod
    def _reject_floats(cls, v: object) -> object:
        if isinstance(v, float):
            raise ValueError("monetary values must be integer minor units, not float")
        if isinstance(v, bool):
            raise ValueError("monetary values must be integers, not booleans")
        return v


class DisputeOffset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    offset_id: str
    record_id: str
    adjustment_cents: int
    reason: str = Field(max_length=512)
    created_at: datetime
