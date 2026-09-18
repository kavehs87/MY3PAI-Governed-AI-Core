"""Coverage closure: service wire path, observability, statements, edge branches."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from schemas.policy import PolicyRequest, PolicyState
from src.accounting.ledger import ContributionLedger, LedgerError, allocate_cents
from src.connectors.registry import ConnectorRegistry, ConnectorState
from src.explanations.statements import (
    custodian_statement_text,
    format_cents,
    run_statement_text,
)
from src.observability.tracing import NdjsonLogger, OtelSpanExporter
from src.policy.gateway import PolicyGateway
from src.policy.rules import HIGH_RISK_ACTIONS, RULE_HASH, RULE_VERSION, rule_hash
from src.policy.tokens import ConsentTokenStore, TokenError
from src.records.mandate import verify_mandate
from src.records.store import AssetNotFound, AssetStore
from src.training.admission import AdmissionError, AdmissionPipeline
from src.workflows.engine import WorkflowEngine
from tests.conftest import CUSTODIAN_ID, CUSTODIAN_KEY, TENANT_ID, make_asset, utcnow


def _req(asset_id: str, request_id: str = "req-x") -> PolicyRequest:
    return PolicyRequest(
        request_id=request_id,
        asset_id=asset_id,
        tenant_id=TENANT_ID,
        action="infer",
        auth_scopes=("infer.basic",),
        consent_tokens=(),
        prompt_text="benign",
        requested_at=utcnow(),
    )


def test_gateway_fail_closed_on_store_error(gateway: PolicyGateway) -> None:
    def boom(_asset_id: str) -> object:
        raise RuntimeError("disk fault")

    gateway._store.get = boom  # type: ignore[assignment]
    decision = gateway.evaluate(_req("whatever", "fail-1"))
    assert decision.state == PolicyState.BLOCKED
    assert "fail-closed" in decision.reason


def test_gateway_consume_skips_bad_token_then_succeeds(
    gateway: PolicyGateway, store: AssetStore, token_store: ConsentTokenStore
) -> None:
    store.register(make_asset(asset_id="mix-consent", requires_consent=True))
    good = token_store.mint("mix-consent", TENANT_ID)
    decision = gateway.evaluate(
        PolicyRequest(
            request_id="mix-1",
            asset_id="mix-consent",
            tenant_id=TENANT_ID,
            action="infer",
            auth_scopes=("infer.basic",),
            consent_tokens=("garbage.token", good),
            prompt_text="benign",
            requested_at=utcnow(),
        )
    )
    assert decision.state == PolicyState.ALLOWED


def test_gateway_consume_race_fails_closed(
    gateway: PolicyGateway, store: AssetStore, token_store: ConsentTokenStore
) -> None:
    store.register(make_asset(asset_id="race-consent", requires_consent=True))
    good = token_store.mint("race-consent", TENANT_ID)

    def always_raise(*_a: object, **_k: object) -> object:
        raise TokenError("token replay detected")

    token_store.verify = always_raise  # type: ignore[assignment]
    decision = gateway.evaluate(
        PolicyRequest(
            request_id="race-1",
            asset_id="race-consent",
            tenant_id=TENANT_ID,
            action="infer",
            auth_scopes=("infer.basic",),
            consent_tokens=(good,),
            prompt_text="benign",
            requested_at=utcnow(),
        )
    )
    assert decision.state == PolicyState.BLOCKED


def test_ledger_rejects_bool_decimal_and_str_money() -> None:
    with pytest.raises(LedgerError):
        allocate_cents(True, [10_000])
    with pytest.raises(LedgerError):
        allocate_cents(Decimal("10"), [10_000])  # type: ignore[arg-type]
    with pytest.raises(LedgerError):
        allocate_cents("10", [10_000])  # type: ignore[arg-type]
    with pytest.raises(LedgerError):
        allocate_cents(-5, [10_000])
    with pytest.raises(LedgerError):
        allocate_cents(100, [])
    with pytest.raises(LedgerError):
        allocate_cents(100, [5000, -1, 5001])
    with pytest.raises(LedgerError):
        allocate_cents(100, [5000, True])


def test_ledger_post_errors_and_replay_drift() -> None:
    ledger = ContributionLedger()
    with pytest.raises(LedgerError):
        ledger.post_run("empty", 100, [])
    ledger.post_run("dup", 100, [("a", "c", 10_000)])
    with pytest.raises(LedgerError):
        ledger.post_run("dup", 100, [("a", "c", 10_000)])
    with pytest.raises(LedgerError):
        ledger.apply_dispute_offset("o", "missing", 10, "x")
    with pytest.raises(LedgerError):
        ledger.apply_dispute_offset("o", "dup:0000", 1.5, "x")  # type: ignore[arg-type]
    with pytest.raises(LedgerError):
        ledger.apply_partial_refund("missing", 1, 2)
    with pytest.raises(LedgerError):
        from src.accounting.ledger import round_half_even_cents

        round_half_even_cents(10, 1, 0)
    with pytest.raises(LedgerError):
        from src.accounting.ledger import round_half_even_cents

        round_half_even_cents(-1, 1, 2)
    with pytest.raises(LedgerError):
        from src.accounting.ledger import round_half_even_cents

        round_half_even_cents(True, 1, 2)
    ledger.post_run("drift", 100, [("a", "c", 10_000)])
    with pytest.raises(LedgerError):
        ledger.replay_run("drift", 200, [("a", "c", 10_000)])
    assert ledger.records_for_run("nope") == []
    assert ledger.statement_for_custodian("ghost") == {
        "gross_cents": 0,
        "adjustments_cents": 0,
        "net_cents": 0,
    }


def test_token_store_edge_branches() -> None:
    with pytest.raises(ValueError):
        ConsentTokenStore(b"short")
    store = ConsentTokenStore(hashlib.sha256(b"edge-key").digest())
    with pytest.raises(TokenError):
        store.verify("not-a-token", "a", "t")
    with pytest.raises(TokenError):
        store.verify("a.b.c", "a", "t")
    token = store.mint("a", "t")
    with pytest.raises(TokenError):
        store.verify(token, "other", "t")
    assert store.used_nonce_count() >= 0
    future = store.mint("a", "t", ttl_seconds=3600)
    import time

    with pytest.raises(TokenError):
        store.verify_without_consuming("bad", "a", "t", now=int(time.time()))
    assert isinstance(future, str)


def test_token_malformed_payload_and_missing_fields() -> None:
    import base64
    import hmac as hm
    import json as js

    key = hashlib.sha256(b"payload-edge").digest()
    store = ConsentTokenStore(key)

    def _b64e(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    bad_payload = _b64e(b"\xff\xfe not json")
    sig = hm.new(key, bad_payload.encode(), hashlib.sha256).digest()
    with pytest.raises(TokenError):
        store.verify(f"{bad_payload}.{_b64e(sig)}", "a", "t")
    thin = _b64e(js.dumps({"asset_id": "a"}).encode())
    sig2 = hm.new(key, thin.encode(), hashlib.sha256).digest()
    with pytest.raises(TokenError):
        store.verify(f"{thin}.{_b64e(sig2)}", "a", "t")
    ahead = store.mint("a", "t", ttl_seconds=3600, now=9_999_999_999)
    with pytest.raises(TokenError):
        store.verify(ahead, "a", "t", now=1_000)


def test_store_edge_branches(store: AssetStore) -> None:
    store.register(make_asset(asset_id="dup-asset"))
    with pytest.raises(ValueError):
        store.register(make_asset(asset_id="dup-asset"))
    with pytest.raises(AssetNotFound):
        store.get("missing")
    with pytest.raises(AssetNotFound):
        store.withdraw("missing")
    assert "dup-asset" in store
    assert len(store) == 1
    assert store.asset_ids() == ["dup-asset"]
    assert store.revoked_at_monotonic_ns("dup-asset") is None
    assert store.utcnow() is not None
    store.withdraw("dup-asset")
    assert store.revoked_at_monotonic_ns("dup-asset") is not None


def test_mandate_invalid_hex_rejected() -> None:
    asset = make_asset(asset_id="hex-check")
    bad = asset.model_copy(update={"mandate_signature": "z" * 64})
    assert verify_mandate(bad, CUSTODIAN_KEY) is False


def test_connector_reactivate_paths() -> None:
    registry = ConnectorRegistry()
    registry.register("c1")
    with pytest.raises(ValueError):
        registry.register("c1")
    assert registry.get("c1").can_serve("anything") is True
    registry.quarantine("asset-z", 123)
    assert registry.get("c1").state == ConnectorState.QUARANTINED
    with pytest.raises(ValueError):
        registry.reactivate("c1")
    registry.acknowledge("c1")
    assert registry.reactivate("c1").state == ConnectorState.ACTIVE


def test_workflow_review_queue_and_output_guards(gateway: PolicyGateway, store: AssetStore) -> None:
    store.register(make_asset(asset_id="pub-asset", usage_constraints=("publish", "infer")))
    engine = WorkflowEngine(gateway)
    run = engine.submit(
        PolicyRequest(
            request_id="pub-1",
            asset_id="pub-asset",
            tenant_id=TENANT_ID,
            action="publish",
            auth_scopes=("infer.basic",),
            consent_tokens=(),
            prompt_text="benign",
            requested_at=utcnow(),
        )
    )
    assert run.state.value == "HeldReview"
    assert engine.pending_review() == 1
    with pytest.raises(PermissionError):
        engine.record_tool_call(run.run_id, "publisher", {})
    with pytest.raises(PermissionError):
        engine.record_output(run.run_id, {})
    with pytest.raises(KeyError):
        engine.release_conditional(_req("pub-asset", "no-hold"))


def test_trace_recorder_accessors(gateway: PolicyGateway, store: AssetStore) -> None:
    store.register(make_asset(asset_id="tr-asset"))
    engine = WorkflowEngine(gateway, trace_id="trace-fixed")
    assert engine.recorder.trace_id == "trace-fixed"
    engine.submit(_req("tr-asset"))
    assert len(engine.recorder.events()) >= 2


def test_statements_and_format() -> None:
    assert format_cents(0) == "0.00"
    assert format_cents(5) == "0.05"
    assert format_cents(-105) == "-1.05"
    ledger = ContributionLedger()
    ledger.post_run("stmt", 300, [("a", "cust-s", 5000), ("b", "cust-s", 5000)])
    text = custodian_statement_text(ledger, "cust-s")
    assert "Net payable: 3.00" in text
    run_text = run_statement_text(ledger, "stmt", 300)
    assert "Run statement: stmt" in run_text


def test_observability_logger_and_spans(tmp_path: Path) -> None:
    log = NdjsonLogger(tmp_path / "events.ndjson")
    record = log.emit("INFO", "gateway.evaluated", {"state": "Allowed"})
    assert record["seq"] == 0
    exporter = OtelSpanExporter()
    span = exporter.start_span("trace-1", "policy.evaluate")
    finished = exporter.end_span(span)
    assert finished["status"] == "OK"
    assert len(exporter.spans()) == 1
    out = tmp_path / "spans.json"
    exporter.write_file(out)
    assert out.exists()


def test_rules_identity() -> None:
    assert RULE_VERSION == "0.2.0"
    assert HIGH_RISK_ACTIONS == ("publish", "train_export", "bulk_export")
    assert rule_hash() == RULE_HASH
    assert len(RULE_HASH) == 64


def test_training_manifest_serialization(tmp_path: Path) -> None:
    pipeline = AdmissionPipeline({CUSTODIAN_ID: CUSTODIAN_KEY})
    assets = [make_asset(asset_id="m-1", usage_constraints=("train",))]
    manifest = pipeline.freeze("build-s", assets, TENANT_ID)
    target = tmp_path / "build_manifest.json"
    manifest.to_json_file(str(target))
    assert target.exists()
    payload = json.loads(target.read_text())
    assert payload["build_id"] == "build-s"
    assert AdmissionPipeline.manifest_to_dict(manifest)["merkle_root"] == manifest.merkle_root
    with pytest.raises(AdmissionError):
        pipeline.freeze(
            "build-bad",
            [make_asset(asset_id="m-2", usage_constraints=("infer",))],
            TENANT_ID,
        )


def test_service_wire_path() -> None:
    from src.policy import service

    gateway, store, _ = service.build_gateway()
    assert gateway is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), service._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Bypass macOS system proxy settings that would otherwise route
    # loopback requests to a dead proxy (urllib honors SystemConfiguration).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        base = f"http://127.0.0.1:{port}"

        def post(path: str, payload: object) -> tuple[int, dict[str, object]]:
            req = urllib.request.Request(
                base + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with opener.open(req, timeout=5) as resp:
                    return (resp.status, json.loads(resp.read().decode()))
            except urllib.error.HTTPError as exc:
                return (exc.code, json.loads(exc.read().decode()))

        def get(path: str) -> int:
            try:
                with opener.open(base + path, timeout=5) as resp:
                    return int(resp.status)
            except urllib.error.HTTPError as exc:
                return int(exc.code)

        assert get("/health") == 200
        assert get("/nope") == 404
        code, body = post(
            "/evaluate",
            {
                "request_id": "http-1",
                "asset_id": "bench-asset-0",
                "tenant_id": "tenant-bench",
                "action": "infer",
                "auth_scopes": ["infer.basic"],
                "prompt_text": "hello",
            },
        )
        assert code == 200
        assert body["state"] == "Allowed"
        code, _ = post("/evaluate", {"request_id": "x"})
        assert code in (200, 400)
        raw_req = urllib.request.Request(base + "/evaluate", data=b"{bad json", method="POST")
        try:
            opener.open(raw_req, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        code, _ = post("/nope", {})
        assert code == 404
        code, body = post("/revoke", {"asset_id": "bench-asset-1"})
        assert code == 200
        assert body["blocked"] is True
        code, _ = post("/revoke", {"asset_id": "missing-asset"})
        assert code == 404
        code, _ = post("/revoke", {"wrong": 1})
        assert code == 400
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_expired_window_boundary(gateway: PolicyGateway, store: AssetStore) -> None:
    now = utcnow()
    asset = make_asset(
        asset_id="boundary",
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(seconds=30),
    )
    store.register(asset)
    decision = gateway.evaluate(_req("boundary", "b-1"), now=now)
    assert decision.state == PolicyState.ALLOWED
