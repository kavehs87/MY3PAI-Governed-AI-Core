"""Deterministic workflow engine.

The engine owns state transitions and the trace recorder; tools and agents
only receive (decision, trace_event) snapshots. Conditional decisions park in
a FIFO holding queue keyed by request; Human Review decisions park in a
review queue keyed by ticket. Execution of a tool call requires an Allowed
decision for the same request_id, otherwise the engine refuses.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from schemas.policy import PolicyDecision, PolicyRequest, PolicyState
from src.policy.gateway import PolicyGateway
from src.records.trace import TraceRecorder


class RunState(str, Enum):
    PENDING = "Pending"
    EXECUTING = "Executing"
    HELD_CONDITIONAL = "HeldConditional"
    HELD_REVIEW = "HeldReview"
    COMPLETED = "Completed"
    REJECTED = "Rejected"


@dataclass
class WorkflowRun:
    run_id: str
    request_id: str
    state: RunState
    decision: PolicyDecision | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)


class WorkflowEngine:
    def __init__(self, gateway: PolicyGateway, trace_id: str | None = None) -> None:
        self._gateway = gateway
        self._recorder = TraceRecorder(trace_id or f"trace-{uuid.uuid4().hex[:12]}")
        self._lock = threading.RLock()
        self._runs: dict[str, WorkflowRun] = {}
        self._conditional_queue: dict[str, WorkflowRun] = {}
        self._review_queue: dict[str, WorkflowRun] = {}

    @property
    def recorder(self) -> TraceRecorder:
        return self._recorder

    def submit(self, request: PolicyRequest) -> WorkflowRun:
        now = datetime.now(timezone.utc)
        decision = self._gateway.evaluate(request, now=now)
        run = WorkflowRun(
            run_id=f"run-{uuid.uuid4().hex[:12]}",
            request_id=request.request_id,
            state=RunState.PENDING,
            decision=decision,
        )
        self._recorder.append(
            request.request_id, "request.received", "workflow", {"action": request.action}
        )
        self._recorder.append(
            request.request_id,
            f"policy.{decision.state.value.lower().replace(' ', '_')}",
            "gateway",
            {"reason": decision.reason, "sequence": decision.sequence},
        )
        with self._lock:
            self._runs[run.run_id] = run
            if decision.state == PolicyState.ALLOWED:
                run.state = RunState.EXECUTING
            elif decision.state == PolicyState.CONDITIONAL:
                run.state = RunState.HELD_CONDITIONAL
                self._conditional_queue[request.request_id] = run
            elif decision.state == PolicyState.HUMAN_REVIEW:
                run.state = RunState.HELD_REVIEW
                assert decision.ticket_id is not None
                self._review_queue[decision.ticket_id] = run
            else:
                run.state = RunState.REJECTED
        return run

    def record_tool_call(self, run_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            run = self._runs[run_id]
            if run.state is not RunState.EXECUTING:
                raise PermissionError(f"run {run_id} is not executing (state={run.state.value})")
            if run.decision is None or run.decision.state is not PolicyState.ALLOWED:
                raise PermissionError("tool execution requires an Allowed decision")
            call: dict[str, Any] = {"tool": tool, "arguments": dict(arguments)}
            run.tool_calls.append(call)
        self._recorder.append(run.request_id, "tool.called", "agent", {"tool": tool})
        return call

    def record_output(self, run_id: str, output: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs[run_id]
            if run.state is not RunState.EXECUTING:
                raise PermissionError("only executing runs accept outputs")
            run.outputs.append(dict(output))
            run.state = RunState.COMPLETED
        self._recorder.append(run.request_id, "run.completed", "workflow", {})

    def release_conditional(self, request: PolicyRequest) -> WorkflowRun:
        """Re-evaluate a held Conditional request after prerequisites arrive."""
        with self._lock:
            held = self._conditional_queue.get(request.request_id)
            if held is None:
                raise KeyError(f"no conditional hold for {request.request_id!r}")
        fresh = self.submit(request)
        with self._lock:
            if fresh.decision is not None and fresh.decision.state == PolicyState.ALLOWED:
                del self._conditional_queue[request.request_id]
        self._recorder.append(request.request_id, "hold.released", "workflow", {})
        return fresh

    def pending_conditional(self) -> int:
        with self._lock:
            return len(self._conditional_queue)

    def pending_review(self) -> int:
        with self._lock:
            return len(self._review_queue)
