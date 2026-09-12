"""Hash-chained trace recorder with monotonic ordering.

The recorder is append-only: every event links to the previous event_hash,
so verifiers detect gaps even when wall clocks skew. Monotonic timestamps
come from time.monotonic_ns; wall_time is recorded for correlation only.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from schemas.trace import GENESIS_HASH, ExecutionTrace, chain_hash


class TraceRecorder:
    def __init__(self, trace_id: str) -> None:
        self._trace_id = trace_id
        self._lock = threading.Lock()
        self._events: list[ExecutionTrace] = []

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def append(
        self,
        request_id: str,
        event: str,
        actor: str,
        detail: dict[str, Any] | None = None,
    ) -> ExecutionTrace:
        with self._lock:
            sequence = len(self._events)
            prev_hash = self._events[-1].event_hash if self._events else GENESIS_HASH
            wall = datetime.now(timezone.utc)
            mono = time.monotonic_ns()
            body: dict[str, Any] = {
                "trace_id": self._trace_id,
                "request_id": request_id,
                "sequence": sequence,
                "wall_time": wall.isoformat(),
                "monotonic_ns": mono,
                "event": event,
                "actor": actor,
                "detail": detail or {},
            }
            record = ExecutionTrace(
                trace_id=self._trace_id,
                request_id=request_id,
                sequence=sequence,
                wall_time=wall,
                monotonic_ns=mono,
                event=event,
                actor=actor,
                detail=detail or {},
                prev_hash=prev_hash,
                event_hash=chain_hash(prev_hash, body),
            )
            self._events.append(record)
            return record

    def events(self) -> list[ExecutionTrace]:
        with self._lock:
            return list(self._events)

    def verify_chain(self) -> bool:
        with self._lock:
            expected = GENESIS_HASH
            last_mono = -1
            for record in self._events:
                if not record.verify_link(expected):
                    return False
                if record.monotonic_ns < last_mono:
                    return False
                last_mono = record.monotonic_ns
                expected = record.event_hash
            return True
