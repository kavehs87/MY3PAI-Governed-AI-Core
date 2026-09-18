"""Schema contract tests: immutability, versioning, hash format."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import __schema_version__
from schemas.asset import AssetMetadata
from schemas.policy import PolicyDecision, PolicyState
from tests.conftest import make_asset, utcnow


def test_asset_schema_version_pinned() -> None:
    asset = make_asset(asset_id="v-check")
    assert asset.schema_version == __schema_version__
    with pytest.raises(ValidationError):
        AssetMetadata(**{**asset.model_dump(), "schema_version": "9.9.9"})


def test_asset_rejects_non_hex_hash() -> None:
    asset = make_asset(asset_id="h-check")
    with pytest.raises(ValidationError):
        AssetMetadata(**{**asset.model_dump(), "content_hash": "xyz"})


def test_asset_rejects_inverted_window() -> None:
    now = utcnow()
    with pytest.raises(ValidationError):
        make_asset(asset_id="w-check", valid_from=now, valid_until=now)


def test_asset_models_immutable() -> None:
    asset = make_asset(asset_id="imm-check")
    with pytest.raises(ValidationError):
        asset.asset_id = "mutated"


def test_policy_decision_holding_invariants() -> None:
    now = utcnow()
    with pytest.raises(ValidationError):
        PolicyDecision(
            request_id="r",
            asset_id="a",
            state=PolicyState.CONDITIONAL,
            rule_version="0.2.0",
            rule_hash="0" * 64,
            required_conditions=(),
            reviewer_trail=(),
            reason="empty conditions",
            evaluated_at=now,
            sequence=0,
        )
    with pytest.raises(ValidationError):
        PolicyDecision(
            request_id="r",
            asset_id="a",
            state=PolicyState.HUMAN_REVIEW,
            rule_version="0.2.0",
            rule_hash="0" * 64,
            required_conditions=(),
            reviewer_trail=(),
            reason="missing ticket",
            evaluated_at=now,
            sequence=0,
        )
