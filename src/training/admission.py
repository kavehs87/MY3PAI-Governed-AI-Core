"""Training admission pipeline with Merkle-pinned build manifests.

An asset is admitted only when its mandate signature verifies under the
custodian key, its validity window covers the build time, it is not
withdrawn, and tenant/action scope permits training. The manifest freezes
the admitted set: manifest_root = Merkle root over sorted admitted
content_hashes, so any post-freeze substitution is detectable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.asset import AssetMetadata
from src.records.mandate import verify_mandate


class AdmissionError(Exception):
    pass


def merkle_root(leaves_hex: list[str]) -> str:
    if not leaves_hex:
        return hashlib.sha256(b"my3pai:empty-build").hexdigest()
    level = sorted(leaves_hex)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            nxt.append(hashlib.sha256((left + right).encode("ascii")).hexdigest())
        level = sorted(nxt)
    return level[0]


class BuildManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    build_id: str
    frozen_at: datetime
    admitted_assets: tuple[str, ...]
    content_hashes: tuple[str, ...]
    merkle_root: str = Field(min_length=64, max_length=64)
    rule_version: str

    def to_json_file(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.model_dump(mode="json"), fh, indent=2, sort_keys=True)


class AdmissionPipeline:
    def __init__(self, custodian_keys: dict[str, bytes], rule_version: str = "0.2.0") -> None:
        self._keys = dict(custodian_keys)
        self._rule_version = rule_version

    def admit(
        self,
        asset: AssetMetadata,
        tenant_id: str,
        build_time: datetime | None = None,
    ) -> None:
        now = build_time or datetime.now(timezone.utc)
        key = self._keys.get(asset.custodian_id)
        if key is None or not verify_mandate(asset, key):
            raise AdmissionError(f"asset {asset.asset_id!r}: mandate not verified")
        if asset.withdrawn:
            raise AdmissionError(f"asset {asset.asset_id!r}: withdrawn")
        if asset.tenant_id != tenant_id:
            raise AdmissionError(f"asset {asset.asset_id!r}: tenant mismatch")
        if now < asset.valid_from or now > asset.valid_until:
            raise AdmissionError(f"asset {asset.asset_id!r}: outside validity window")
        if asset.usage_constraints and "train" not in asset.usage_constraints:
            raise AdmissionError(f"asset {asset.asset_id!r}: training not in usage constraints")

    def freeze(
        self,
        build_id: str,
        admitted: list[AssetMetadata],
        tenant_id: str,
        build_time: datetime | None = None,
    ) -> BuildManifest:
        now = build_time or datetime.now(timezone.utc)
        for asset in admitted:
            self.admit(asset, tenant_id, build_time=now)
        ordered = sorted(admitted, key=lambda a: a.asset_id)
        hashes = [a.content_hash for a in ordered]
        manifest = BuildManifest(
            build_id=build_id,
            frozen_at=now,
            admitted_assets=tuple(a.asset_id for a in ordered),
            content_hashes=tuple(hashes),
            merkle_root=merkle_root(hashes),
            rule_version=self._rule_version,
        )
        return manifest

    @staticmethod
    def verify_manifest(manifest: BuildManifest) -> bool:
        return manifest.merkle_root == merkle_root(list(manifest.content_hashes))

    @staticmethod
    def manifest_to_dict(manifest: BuildManifest) -> dict[str, Any]:
        return manifest.model_dump(mode="json")
