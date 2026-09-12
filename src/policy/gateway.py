"""Four-state deterministic policy gateway.

Evaluation order is fixed and fail-closed: the first matching terminal rule
decides, any unexpected error maps to Blocked, and the agent runtime never
receives rule material or store handles that would allow permission mutation.
"""

from __future__ import annotations

import itertools
import secrets
import threading
from datetime import datetime, timezone

from schemas.policy import PolicyDecision, PolicyRequest, PolicyState
from src.policy import injection
from src.policy.rules import HIGH_RISK_ACTIONS, RULE_HASH, RULE_VERSION
from src.policy.tokens import ConsentTokenStore, TokenError
from src.records.mandate import verify_mandate
from src.records.store import AssetNotFound, AssetStore


class PolicyGateway:
    def __init__(
        self,
        store: AssetStore,
        token_store: ConsentTokenStore,
        custodian_keys: dict[str, bytes],
    ) -> None:
        self._store = store
        self._tokens = token_store
        self._custodian_keys = dict(custodian_keys)
        self._counter = itertools.count()
        self._lock = threading.Lock()

    def _next_sequence(self) -> int:
        with self._lock:
            return next(self._counter)

    def _blocked(
        self, request: PolicyRequest, reason: str, now: datetime, asset_id: str | None = None
    ) -> PolicyDecision:
        return PolicyDecision(
            request_id=request.request_id,
            asset_id=asset_id or request.asset_id,
            state=PolicyState.BLOCKED,
            rule_version=RULE_VERSION,
            rule_hash=RULE_HASH,
            required_conditions=(),
            reviewer_trail=(),
            reason=reason,
            ticket_id=None,
            evaluated_at=now,
            sequence=self._next_sequence(),
        )

    def evaluate(self, request: PolicyRequest, now: datetime | None = None) -> PolicyDecision:
        current = now or datetime.now(timezone.utc)
        try:
            return self._evaluate_inner(request, current)
        except Exception as exc:
            return self._blocked(
                request, f"fail-closed on internal error: {type(exc).__name__}", current
            )

    def _evaluate_inner(self, request: PolicyRequest, now: datetime) -> PolicyDecision:
        try:
            asset = self._store.get(request.asset_id)
        except AssetNotFound:
            return self._blocked(request, "unknown asset_id", now)

        if asset.withdrawn:
            return self._blocked(request, "asset rights withdrawn", now, asset.asset_id)

        key = self._custodian_keys.get(asset.custodian_id)
        if key is None or not verify_mandate(asset, key):
            return self._blocked(request, "mandate signature invalid", now, asset.asset_id)

        if request.tenant_id != asset.tenant_id:
            return self._blocked(request, "cross-tenant access denied", now, asset.asset_id)

        if now < asset.valid_from:
            return self._blocked(request, "rights not yet valid", now, asset.asset_id)
        if now > asset.valid_until:
            return self._blocked(request, "rights expired", now, asset.asset_id)

        if injection.contains_injection(request.prompt_text):
            return self._blocked(request, "prompt injection rejected", now, asset.asset_id)

        granted = set(asset.granted_scopes)
        for scope in request.auth_scopes:
            if scope not in granted:
                return self._blocked(
                    request,
                    f"privilege escalation: scope {scope!r} not granted",
                    now,
                    asset.asset_id,
                )

        if asset.usage_constraints and request.action not in asset.usage_constraints:
            return self._blocked(
                request, f"action {request.action!r} outside usage constraints", now, asset.asset_id
            )

        if asset.requires_consent:
            if not request.consent_tokens:
                return PolicyDecision(
                    request_id=request.request_id,
                    asset_id=asset.asset_id,
                    state=PolicyState.CONDITIONAL,
                    rule_version=RULE_VERSION,
                    rule_hash=RULE_HASH,
                    required_conditions=("consent_token:valid",),
                    reviewer_trail=(),
                    reason="awaiting valid consent token",
                    ticket_id=None,
                    evaluated_at=now,
                    sequence=self._next_sequence(),
                )
            probe_now = int(now.timestamp())
            tamper: TokenError | None = None
            for token in request.consent_tokens:
                try:
                    self._tokens.verify_without_consuming(
                        token, asset.asset_id, asset.tenant_id, now=probe_now
                    )
                except TokenError as exc:
                    tamper = exc
                    continue
                break
            else:
                message = str(tamper) if tamper else "consent validation failed"
                if "replay" in message:
                    return self._blocked(
                        request, "consent token replay detected", now, asset.asset_id
                    )
                if "expired" in message:
                    return self._blocked(request, "consent token expired", now, asset.asset_id)
                return self._blocked(
                    request, f"consent token invalid: {message}", now, asset.asset_id
                )
            consumed = False
            for token in request.consent_tokens:
                try:
                    self._tokens.verify(token, asset.asset_id, asset.tenant_id, now=probe_now)
                except TokenError:
                    continue
                consumed = True
                break
            if not consumed:
                return self._blocked(request, "consent token replay detected", now, asset.asset_id)

        if request.action in HIGH_RISK_ACTIONS:
            return PolicyDecision(
                request_id=request.request_id,
                asset_id=asset.asset_id,
                state=PolicyState.HUMAN_REVIEW,
                rule_version=RULE_VERSION,
                rule_hash=RULE_HASH,
                required_conditions=(),
                reviewer_trail=(),
                reason=f"high-risk action {request.action!r} requires human review",
                ticket_id=f"REV-{secrets.token_hex(8).upper()}",
                evaluated_at=now,
                sequence=self._next_sequence(),
            )

        return PolicyDecision(
            request_id=request.request_id,
            asset_id=asset.asset_id,
            state=PolicyState.ALLOWED,
            rule_version=RULE_VERSION,
            rule_hash=RULE_HASH,
            required_conditions=(),
            reviewer_trail=(),
            reason="within assessed scope",
            ticket_id=None,
            evaluated_at=now,
            sequence=self._next_sequence(),
        )
