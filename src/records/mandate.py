"""Mandate signature binding for registered asset bytes.

Signature = HMAC-SHA256(custodian_key, asset_id || content_hash ||
custodian_id || tenant_id || valid_from || valid_until || rights_mandate ||
usage_constraints || granted_scopes || requires_consent). The gateway
recomputes it on every evaluation, so database tampering that alters scope,
constraints, or validity without the custodian key fails closed.
"""

from __future__ import annotations

import hashlib
import hmac

from schemas.asset import AssetMetadata


def mandate_message(asset: AssetMetadata) -> bytes:
    parts = [
        asset.asset_id,
        asset.content_hash,
        asset.custodian_id,
        asset.tenant_id,
        asset.valid_from.isoformat(),
        asset.valid_until.isoformat(),
        asset.rights_mandate,
        ",".join(sorted(asset.usage_constraints)),
        ",".join(sorted(asset.granted_scopes)),
        "consent" if asset.requires_consent else "no-consent",
    ]
    return "|".join(parts).encode("utf-8")


def sign_mandate(asset: AssetMetadata, custodian_key: bytes) -> str:
    return hmac.new(custodian_key, mandate_message(asset), hashlib.sha256).hexdigest()


def verify_mandate(asset: AssetMetadata, custodian_key: bytes) -> bool:
    expected = hmac.new(custodian_key, mandate_message(asset), hashlib.sha256).digest()
    try:
        provided = bytes.fromhex(asset.mandate_signature)
    except ValueError:
        return False
    return hmac.compare_digest(provided, expected)
