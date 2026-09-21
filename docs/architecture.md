# Architecture

## Request path

`Actor + Declared Purpose` → `Policy Gateway` → `Four-State Engine` →
`Scoped Execution` → `Audit Trail & Fixed-Point Accounting Ledger`,
with `Revocation Cutoff` and `Connector Quarantine` as circuit breakers
(see README mermaid diagram).

## Four-state engine

`PolicyGateway.evaluate` (`src/policy/gateway.py`) applies 11 ordered rules
(`src/policy/rules.py`, `RULE_VERSION 0.2.0`, hash-pinned). First terminal
match decides; any exception maps to `Blocked`. States:

- `Allowed`: execute strictly within assessed scope.
- `Conditional`: parked in a FIFO holding queue until a valid consent token
  arrives (`WorkflowEngine.release_conditional` re-evaluates).
- `Blocked`: terminal, with immutable reason string.
- `Human Review`: parked under a `REV-*` ticket for high-risk actions
  (`publish`, `train_export`, `bulk_export`).

## Mandate binding

`mandate = HMAC-SHA256(custodian_key, asset_id || content_hash ||
custodian_id || tenant_id || valid_from || valid_until || rights_mandate ||
usage_constraints || granted_scopes || consent_flag)` (`src/records/mandate.py`).
The gateway recomputes it per evaluation with constant-time comparison, so
record tampering without the custodian key fails closed.

## Consent tokens

`b64url(payload).b64url(HMAC-SHA256(token_key, payload))`
(`src/policy/tokens.py`). Single-use nonces; verification is constant-time;
nonces burn only on fully valid verification. Missing token → `Conditional`;
tampered, expired, wrong-scope, or replayed → `Blocked`.

## Quarantine protocol

`AssetStore.withdraw` marks the asset withdrawn and synchronously notifies
listeners before returning. `ConnectorRegistry.quarantine` flips all
connectors to `Quarantined`, refusing reads until explicit `acknowledge()`
after revalidation. The next gateway evaluation blocks without polling.

## Trace and ledger

`TraceRecorder` appends `event_hash = SHA256(prev_hash || canonical_json(body))`
with monotonic sequencing (`src/records/trace.py`). `ContributionLedger`
allocates integer cents over integer basis-point weights by exact
largest-remainder distribution, ties to lowest index
(`src/accounting/ledger.py`); `replay_run` reproduces records byte-for-byte.
