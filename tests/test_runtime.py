"""Workflow engine, trace chain, quarantine, training, tokens, fuzz."""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from schemas.policy import PolicyRequest, PolicyState
from src.accounting.ledger import allocate_cents
from src.connectors.registry import ConnectorRegistry, ConnectorState
from src.discovery.catalog import AssetDiscovery
from src.policy.gateway import PolicyGateway
from src.policy.tokens import ConsentTokenStore
from src.records.store import AssetStore
from src.training.admission import AdmissionPipeline, merkle_root
from src.workflows.engine import RunState, WorkflowEngine
from tests.conftest import CUSTODIAN_ID, TENANT_ID, make_asset, utcnow


def _req(asset_id: str, request_id: str = "req-1") -> PolicyRequest:
    return PolicyRequest(
        request_id=request_id,
        asset_id=asset_id,
        tenant_id=TENANT_ID,
        action="infer",
        auth_scopes=("infer.basic",),
        consent_tokens=(),
        prompt_text="benign render task",
        requested_at=utcnow(),
    )


def test_workflow_allowed_executes_and_completes(gateway: PolicyGateway, store: AssetStore) -> None:
    store.register(make_asset(asset_id="wf-ok"))
    engine = WorkflowEngine(gateway)
    run = engine.submit(_req("wf-ok"))
    assert run.state == RunState.EXECUTING
    engine.record_tool_call(run.run_id, "renderer", {"q": "sky"})
    engine.record_output(run.run_id, {"image": "png-bytes"})
    assert engine.recorder.verify_chain() is True


def test_workflow_blocked_rejects_tool_calls(gateway: PolicyGateway, store: AssetStore) -> None:
    engine = WorkflowEngine(gateway)
    run = engine.submit(_req("missing-asset"))
    assert run.state == RunState.REJECTED
    assert run.decision is not None and run.decision.state == PolicyState.BLOCKED
    with pytest.raises(PermissionError):
        engine.record_tool_call(run.run_id, "renderer", {})


def test_workflow_conditional_hold_and_release(
    gateway: PolicyGateway, store: AssetStore, token_store: ConsentTokenStore
) -> None:
    store.register(make_asset(asset_id="wf-hold", requires_consent=True))
    engine = WorkflowEngine(gateway)
    held = engine.submit(_req("wf-hold", "hold-1"))
    assert held.state == RunState.HELD_CONDITIONAL
    assert engine.pending_conditional() == 1
    token = token_store.mint("wf-hold", TENANT_ID)
    released = engine.release_conditional(
        PolicyRequest(
            request_id="hold-1",
            asset_id="wf-hold",
            tenant_id=TENANT_ID,
            action="infer",
            auth_scopes=("infer.basic",),
            consent_tokens=(token,),
            prompt_text="benign",
            requested_at=utcnow(),
        )
    )
    assert released.decision is not None
    assert released.decision.state == PolicyState.ALLOWED


def test_withdrawal_quarantines_connectors(gateway: PolicyGateway, store: AssetStore) -> None:
    registry = ConnectorRegistry()
    registry.register("edge-1")
    registry.register("edge-2")
    store.add_revocation_listener(registry.quarantine)
    store.register(make_asset(asset_id="q-asset"))
    store.withdraw("q-asset")
    for cid in registry.connector_ids():
        connector = registry.get(cid)
        assert connector.state == ConnectorState.QUARANTINED
        assert "q-asset" in connector.quarantined_assets
        assert connector.can_serve("q-asset") is False
    registry.acknowledge("edge-1")
    assert registry.get("edge-1").state == ConnectorState.ACKNOWLEDGED


def test_withdrawal_propagation_bound(gateway: PolicyGateway, store: AssetStore) -> None:
    store.register(make_asset(asset_id="race-asset"))
    store.withdraw("race-asset")
    t0 = time.monotonic_ns()
    decision = gateway.evaluate(_req("race-asset", "race-1"))
    elapsed_ms = (time.monotonic_ns() - t0) / 1_000_000.0
    assert decision.state == PolicyState.BLOCKED
    assert elapsed_ms < 50.0


def test_concurrent_withdrawal_blocks_all_workers(
    gateway: PolicyGateway, store: AssetStore
) -> None:
    store.register(make_asset(asset_id="conc-asset"))
    store.withdraw("conc-asset")
    outcomes: list[PolicyState] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        decision = gateway.evaluate(_req("conc-asset", f"conc-{i}"))
        with lock:
            outcomes.append(decision.state)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes == [PolicyState.BLOCKED] * 32


def test_trace_chain_detects_tamper(gateway: PolicyGateway, store: AssetStore) -> None:
    store.register(make_asset(asset_id="trace-asset"))
    engine = WorkflowEngine(gateway)
    run = engine.submit(_req("trace-asset"))
    engine.record_tool_call(run.run_id, "renderer", {})
    assert engine.recorder.verify_chain() is True
    events = engine.recorder.events()
    assert events[1].prev_hash == events[0].event_hash
    assert all(e.monotonic_ns >= events[0].monotonic_ns for e in events)


def test_discovery_hides_withdrawn_by_default(gateway: PolicyGateway, store: AssetStore) -> None:
    store.register(make_asset(asset_id="d-1"))
    store.register(make_asset(asset_id="d-2"))
    store.withdraw("d-2")
    discovery = AssetDiscovery(store)
    assert [a.asset_id for a in discovery.search(TENANT_ID)] == ["d-1"]
    assert len(discovery.search(TENANT_ID, include_withdrawn=True)) == 2
    assert discovery.search("tenant-other") == []


def test_training_admission_and_manifest() -> None:
    from tests.conftest import CUSTODIAN_KEY

    pipeline = AdmissionPipeline({CUSTODIAN_ID: CUSTODIAN_KEY})
    assets = [
        make_asset(
            asset_id=f"t-{i}", usage_constraints=("train", "infer"), content_seed=f"bytes-{i}"
        )
        for i in range(4)
    ]
    manifest = pipeline.freeze("build-1", assets, TENANT_ID)
    assert AdmissionPipeline.verify_manifest(manifest) is True
    assert manifest.merkle_root == merkle_root([a.content_hash for a in assets])


def test_training_rejects_unsigned_and_withdrawn() -> None:
    from src.training.admission import AdmissionError
    from tests.conftest import CUSTODIAN_KEY

    pipeline = AdmissionPipeline({CUSTODIAN_ID: CUSTODIAN_KEY})
    unsigned = make_asset(asset_id="unsigned", custodian_id="unknown-custodian")
    with pytest.raises(AdmissionError):
        pipeline.admit(unsigned, TENANT_ID)
    expired = make_asset(
        asset_id="old",
        usage_constraints=("train",),
        valid_from=utcnow() - timedelta(days=90),
        valid_until=utcnow() - timedelta(days=1),
    )
    with pytest.raises(AdmissionError):
        pipeline.admit(expired, TENANT_ID)


def test_merkle_root_empty_build() -> None:
    root = merkle_root([])
    assert root == hashlib.sha256(b"my3pai:empty-build").hexdigest()


@given(
    revenue=st.integers(min_value=0, max_value=1_000_000),
    w1=st.integers(min_value=0, max_value=10_000),
)
@settings(max_examples=100, deadline=None)
def test_fuzz_allocation_conserves_revenue(revenue: int, w1: int) -> None:
    w2 = 10_000 - w1
    shares = allocate_cents(revenue, [w1, w2])
    assert sum(shares) == revenue
    assert all(s >= 0 for s in shares)


@given(text=st.text(max_size=200))
@settings(max_examples=150, deadline=None)
def test_fuzz_gateway_never_raises(text: str) -> None:
    store = AssetStore()
    tokens = ConsentTokenStore(hashlib.sha256(b"fuzz-key-material-00000001").digest())
    gateway = PolicyGateway(store, tokens, {})
    store.register(make_asset(asset_id="fuzz-asset"))
    request = PolicyRequest(
        request_id="fuzz",
        asset_id="fuzz-asset",
        tenant_id=TENANT_ID,
        action="infer",
        auth_scopes=("infer.basic",),
        consent_tokens=(),
        prompt_text=text[:8192],
        requested_at=utcnow(),
    )
    decision = gateway.evaluate(request)
    assert decision.state in (
        PolicyState.ALLOWED,
        PolicyState.CONDITIONAL,
        PolicyState.BLOCKED,
        PolicyState.HUMAN_REVIEW,
    )
