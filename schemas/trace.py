"""Hash-chained execution trace contract.

Each record commits to its predecessor via event_hash =
SHA256(prev_hash || canonical_json(event body)). Any reordering, deletion, or
mutation breaks the chain, which the verifier detects without trusted clocks:
wall_time is informational, monotonic_ns orders transitions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(prev_hash.encode("ascii"))
    digest.update(canonical_json(payload).encode("utf-8"))
    return digest.hexdigest()


GENESIS_HASH = "0" * 64


class ExecutionTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: str
    request_id: str
    sequence: int = Field(ge=0)
    wall_time: datetime
    monotonic_ns: int = Field(ge=0)
    event: str = Field(min_length=1, max_length=64)
    actor: str = Field(min_length=1, max_length=64)
    detail: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = Field(min_length=64, max_length=64)
    event_hash: str = Field(min_length=64, max_length=64)

    def verify_link(self, expected_prev: str) -> bool:
        if self.prev_hash != expected_prev:
            return False
        body: dict[str, Any] = {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "sequence": self.sequence,
            "wall_time": self.wall_time.isoformat(),
            "monotonic_ns": self.monotonic_ns,
            "event": self.event,
            "actor": self.actor,
            "detail": self.detail,
        }
        return chain_hash(self.prev_hash, body) == self.event_hash
