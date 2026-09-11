"""Versioned AssetMetadata contract.

Invariant: content_hash binds the registered bytes; mandate_signature binds
(asset_id, content_hash, custodian_id, tenant_id, validity window) under the
custodian HMAC key. Verification uses constant-time comparison at the gateway
boundary, never inside agent-controlled code.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas import __schema_version__

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AssetMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default=__schema_version__)
    asset_id: str = Field(min_length=1, max_length=128)
    custodian_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    content_hash: str = Field(min_length=64, max_length=64)
    rights_mandate: str = Field(min_length=1, max_length=256)
    mandate_signature: str = Field(min_length=64, max_length=64)
    usage_constraints: tuple[str, ...] = Field(default=())
    granted_scopes: tuple[str, ...] = Field(default=())
    requires_consent: bool = False
    valid_from: datetime
    valid_until: datetime
    withdrawn: bool = False

    @field_validator("content_hash", "mandate_signature")
    @classmethod
    def _must_be_sha256_hex(cls, v: str) -> str:
        lowered = v.lower()
        if not _HEX64.match(lowered):
            raise ValueError("expected lowercase sha256 hex digest")
        return lowered

    @field_validator("schema_version")
    @classmethod
    def _version_must_match(cls, v: str) -> str:
        if v != __schema_version__:
            raise ValueError(f"unsupported schema_version {v!r}")
        return v

    def model_post_init(self, _ctx: object) -> None:
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be strictly after valid_from")
