"""Minimal-latency HTTP gateway service for policy evaluation.

Stdlib http.server with a thread pool backend: no framework overhead, which
keeps p95 evaluation latency in the single-digit millisecond range on
localhost. Endpoints: GET /health, POST /evaluate, POST /revoke. The service
mirrors the in-process PolicyGateway exactly; k6 measures this wire path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from schemas.policy import PolicyRequest
from src.policy.gateway import PolicyGateway
from src.policy.tokens import ConsentTokenStore
from src.records.mandate import sign_mandate
from src.records.store import AssetStore

SHARED_TOKEN_KEY = hashlib.sha256(b"my3pai-beta-gateway-token-key").digest()

_BENCH_ASSETS: list[dict[str, object]] = []


def _seed_assets(store: AssetStore, custodian_keys: dict[str, bytes]) -> None:
    from schemas.asset import AssetMetadata

    now = datetime.now(timezone.utc)
    for i in range(8):
        asset_id = f"bench-asset-{i}"
        draft = AssetMetadata(
            asset_id=asset_id,
            custodian_id="custodian-bench",
            tenant_id="tenant-bench",
            content_hash=hashlib.sha256(f"bench-bytes-{i}".encode()).hexdigest(),
            rights_mandate=f"mandate-{i}",
            mandate_signature="0" * 64,
            usage_constraints=("infer", "render"),
            granted_scopes=("infer.basic",),
            requires_consent=False,
            valid_from=now - timedelta(days=30),
            valid_until=now + timedelta(days=30),
            withdrawn=False,
        )
        signed = draft.model_copy(
            update={"mandate_signature": sign_mandate(draft, custodian_keys["custodian-bench"])}
        )
        if asset_id not in store:
            store.register(signed)


def build_gateway() -> tuple[PolicyGateway, AssetStore, ConsentTokenStore]:
    store = AssetStore()
    custodian_keys = {"custodian-bench": hashlib.sha256(b"custodian-bench-key").digest()}
    _seed_assets(store, custodian_keys)
    tokens = ConsentTokenStore(SHARED_TOKEN_KEY)
    gateway = PolicyGateway(store, tokens, custodian_keys)
    return gateway, store, tokens


_GATEWAY_STATE: dict[str, object] = {}
_STATE_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=32)


def _gateway() -> PolicyGateway:
    with _STATE_LOCK:
        gw = _GATEWAY_STATE.get("gateway")
        if gw is None:
            gateway, store, _ = build_gateway()
            _GATEWAY_STATE["gateway"] = gateway
            _GATEWAY_STATE["store"] = store
            gw = gateway
        assert isinstance(gw, PolicyGateway)
        return gw


def _store() -> AssetStore:
    _gateway()
    with _STATE_LOCK:
        store = _GATEWAY_STATE["store"]
        assert isinstance(store, AssetStore)
        return store


class _Handler(BaseHTTPRequestHandler):
    server_version = "MY3PAI-Gateway/0.2.0"

    def log_message(self, format: object, *args: object) -> None:  # noqa: A002
        pass

    def _send_json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"

        def work() -> tuple[int, dict[str, object]]:
            if self.path == "/evaluate":
                return self._handle_evaluate(raw)
            if self.path == "/revoke":
                return self._handle_revoke(raw)
            return (404, {"error": "not found"})

        future = _POOL.submit(work)
        code, payload = future.result()
        self._send_json(code, payload)

    def _handle_evaluate(self, raw: bytes) -> tuple[int, dict[str, object]]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return (400, {"error": "invalid json"})
        try:
            request = PolicyRequest(
                request_id=str(data.get("request_id", f"req-{time.monotonic_ns()}")),
                asset_id=str(data.get("asset_id", "bench-asset-0")),
                tenant_id=str(data.get("tenant_id", "tenant-bench")),
                action=str(data.get("action", "infer")),
                auth_scopes=tuple(data.get("auth_scopes", ["infer.basic"])),
                consent_tokens=tuple(data.get("consent_tokens", [])),
                prompt_text=str(data.get("prompt_text", "render a landscape")),
                requested_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            return (400, {"error": f"invalid request: {exc}"})
        decision = _gateway().evaluate(request)
        return (
            200,
            {
                "state": decision.state.value,
                "reason": decision.reason,
                "sequence": decision.sequence,
                "rule_version": decision.rule_version,
            },
        )

    def _handle_revoke(self, raw: bytes) -> tuple[int, dict[str, object]]:
        try:
            data = json.loads(raw.decode("utf-8"))
            asset_id = str(data["asset_id"])
        except Exception:
            return (400, {"error": "asset_id required"})
        t0 = time.monotonic_ns()
        try:
            _store().withdraw(asset_id)
        except Exception as exc:
            return (404, {"error": str(exc)})
        # Immediate probe: next evaluation must already block.
        from schemas.policy import PolicyRequest as PR

        probe = PR(
            request_id=f"probe-{t0}",
            asset_id=asset_id,
            tenant_id="tenant-bench",
            action="infer",
            auth_scopes=("infer.basic",),
            consent_tokens=(),
            prompt_text="probe",
            requested_at=datetime.now(timezone.utc),
        )
        blocked = _gateway().evaluate(probe).state.value == "Blocked"
        elapsed_ms = (time.monotonic_ns() - t0) / 1_000_000.0
        return (200, {"revoked": True, "blocked": blocked, "revoke_to_block_ms": elapsed_ms})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8410)
    args = parser.parse_args()
    _gateway()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    server.daemon_threads = True
    print(f"MY3PAI gateway listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
