"""HMAC consent tokens with single-use nonce replay defense.

Token wire format: b64url(payload_json) + "." + b64url(signature), where
signature = HMAC-SHA256(token_key, payload_b64). Payload carries asset scope,
tenant scope, nonce, and expiry. Verification is constant-time; nonces are
marked used only after a fully valid verification so failed attempts cannot
burn honest tokens, while successful replays are rejected.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass(frozen=True)
class ConsentClaim:
    asset_id: str
    tenant_id: str
    nonce: str
    issued_at: int
    expires_at: int


class TokenError(Exception):
    pass


class ConsentTokenStore:
    def __init__(self, token_key: bytes) -> None:
        if len(token_key) < 32:
            raise ValueError("token_key must be at least 32 bytes")
        self._key = bytes(token_key)
        self._lock = threading.Lock()
        self._used_nonces: set[str] = set()

    def mint(
        self,
        asset_id: str,
        tenant_id: str,
        ttl_seconds: int = 3600,
        now: int | None = None,
    ) -> str:
        issued = int(now if now is not None else time.time())
        payload = {
            "asset_id": asset_id,
            "tenant_id": tenant_id,
            "nonce": secrets.token_hex(16),
            "iat": issued,
            "exp": issued + int(ttl_seconds),
        }
        payload_b64 = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        sig = hmac.new(self._key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        return f"{payload_b64}.{_b64e(sig)}"

    def verify(
        self,
        token: str,
        expected_asset_id: str,
        expected_tenant_id: str,
        now: int | None = None,
    ) -> ConsentClaim:
        current = int(now if now is not None else time.time())
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            payload_raw = _b64d(payload_b64)
            provided_sig = _b64d(sig_b64)
        except Exception as exc:
            raise TokenError("malformed token") from exc
        expected_sig = hmac.new(self._key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise TokenError("invalid signature")
        try:
            payload = json.loads(payload_raw.decode("utf-8"))
        except Exception as exc:
            raise TokenError("malformed payload") from exc
        try:
            claim = ConsentClaim(
                asset_id=str(payload["asset_id"]),
                tenant_id=str(payload["tenant_id"]),
                nonce=str(payload["nonce"]),
                issued_at=int(payload["iat"]),
                expires_at=int(payload["exp"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise TokenError("missing claim fields") from exc
        if not hmac.compare_digest(claim.asset_id, expected_asset_id):
            raise TokenError("asset scope mismatch")
        if not hmac.compare_digest(claim.tenant_id, expected_tenant_id):
            raise TokenError("tenant scope mismatch")
        if current >= claim.expires_at:
            raise TokenError("token expired")
        if claim.issued_at > current + 60:
            raise TokenError("token issued in the future")
        with self._lock:
            if claim.nonce in self._used_nonces:
                raise TokenError("token replay detected")
            self._used_nonces.add(claim.nonce)
        return claim

    def verify_without_consuming(
        self,
        token: str,
        expected_asset_id: str,
        expected_tenant_id: str,
        now: int | None = None,
    ) -> ConsentClaim:
        """Validate shape/signature/expiry/scope without burning the nonce.

        Used when the gateway must distinguish tamper (Blocked) from absence
        before committing to consume a token on the allow path.
        """
        current = int(now if now is not None else time.time())
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            provided_sig = _b64d(sig_b64)
            payload_raw = _b64d(payload_b64)
        except Exception as exc:
            raise TokenError("malformed token") from exc
        expected_sig = hmac.new(self._key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(provided_sig, expected_sig):
            raise TokenError("invalid signature")
        payload = json.loads(payload_raw.decode("utf-8"))
        claim = ConsentClaim(
            asset_id=str(payload["asset_id"]),
            tenant_id=str(payload["tenant_id"]),
            nonce=str(payload["nonce"]),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
        )
        if claim.asset_id != expected_asset_id:
            raise TokenError("asset scope mismatch")
        if claim.tenant_id != expected_tenant_id:
            raise TokenError("tenant scope mismatch")
        if current >= claim.expires_at:
            raise TokenError("token expired")
        with self._lock:
            if claim.nonce in self._used_nonces:
                raise TokenError("token replay detected")
        return claim

    def used_nonce_count(self) -> int:
        with self._lock:
            return len(self._used_nonces)
