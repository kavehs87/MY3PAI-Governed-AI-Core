"""Four-state policy decision contract.

The gateway is the sole writer of PolicyDecision. Agents receive a read-only
copy and hold no reference to rule material, which enforces hard isolation
between execution and authorization.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas import __schema_version__


class PolicyState(str, Enum):
    ALLOWED = "Allowed"
    CONDITIONAL = "Conditional"
    BLOCKED = "Blocked"
    HUMAN_REVIEW = "Human Review"


class PolicyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    asset_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=64)
    auth_scopes: tuple[str, ...] = Field(default=())
    consent_tokens: tuple[str, ...] = Field(default=())
    prompt_text: str = Field(default="", max_length=8192)
    requested_at: datetime


class ReviewerEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_id: str
    decided_at: datetime
    note: str = Field(max_length=1024)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default=__schema_version__)
    request_id: str
    asset_id: str
    state: PolicyState
    rule_version: str
    rule_hash: str = Field(min_length=64, max_length=64)
    required_conditions: tuple[str, ...] = Field(default=())
    reviewer_trail: tuple[ReviewerEntry, ...] = Field(default=())
    reason: str = Field(max_length=1024)
    ticket_id: str | None = None
    evaluated_at: datetime
    sequence: int = Field(ge=0)

    @field_validator("schema_version")
    @classmethod
    def _version_must_match(cls, v: str) -> str:
        if v != __schema_version__:
            raise ValueError(f"unsupported schema_version {v!r}")
        return v

    def model_post_init(self, _ctx: object) -> None:
        # Holding states must carry either pending conditions or a review ticket;
        # terminal states must not, so a forged "Allowed" cannot smuggle queue state.
        if self.state == PolicyState.CONDITIONAL and not self.required_conditions:
            raise ValueError("Conditional decisions require required_conditions")
        if self.state == PolicyState.HUMAN_REVIEW and not self.ticket_id:
            raise ValueError("Human Review decisions require ticket_id")
        if self.state in (PolicyState.ALLOWED, PolicyState.BLOCKED) and self.ticket_id:
            raise ValueError("terminal states must not carry ticket_id")
