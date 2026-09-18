"""Shared fixtures: deterministic keys, signed assets, gateway harness."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from schemas.asset import AssetMetadata
from src.policy.gateway import PolicyGateway
from src.policy.tokens import ConsentTokenStore
from src.records.mandate import sign_mandate
from src.records.store import AssetStore

CUSTODIAN_ID = "custodian-test"
TENANT_ID = "tenant-test"
CUSTODIAN_KEY = hashlib.sha256(b"test-custodian-key").digest()
TOKEN_KEY = hashlib.sha256(b"test-token-key").digest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_asset(
    asset_id: str | None = None,
    custodian_id: str = CUSTODIAN_ID,
    tenant_id: str = TENANT_ID,
    usage_constraints: tuple[str, ...] = ("infer", "render"),
    granted_scopes: tuple[str, ...] = ("infer.basic",),
    requires_consent: bool = False,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    withdrawn: bool = False,
    content_seed: str | None = None,
) -> AssetMetadata:
    now = utcnow()
    aid = asset_id or f"asset-{uuid.uuid4().hex[:8]}"
    seed = content_seed or aid
    draft = AssetMetadata(
        asset_id=aid,
        custodian_id=custodian_id,
        tenant_id=tenant_id,
        content_hash=hashlib.sha256(seed.encode()).hexdigest(),
        rights_mandate=f"mandate-{aid}",
        mandate_signature="0" * 64,
        usage_constraints=usage_constraints,
        granted_scopes=granted_scopes,
        requires_consent=requires_consent,
        valid_from=valid_from or (now - timedelta(days=30)),
        valid_until=valid_until or (now + timedelta(days=30)),
        withdrawn=withdrawn,
    )
    if custodian_id == CUSTODIAN_ID:
        return draft.model_copy(update={"mandate_signature": sign_mandate(draft, CUSTODIAN_KEY)})
    return draft


@pytest.fixture
def store() -> AssetStore:
    return AssetStore()


@pytest.fixture
def token_store() -> ConsentTokenStore:
    return ConsentTokenStore(TOKEN_KEY)


@pytest.fixture
def gateway(store: AssetStore, token_store: ConsentTokenStore) -> PolicyGateway:
    return PolicyGateway(store, token_store, {CUSTODIAN_ID: CUSTODIAN_KEY})


@pytest.fixture
def registered_asset(store: AssetStore) -> AssetMetadata:
    asset = make_asset(asset_id="asset-baseline")
    store.register(asset)
    return asset
