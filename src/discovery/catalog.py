"""Asset discovery over the registered catalog.

Read-only index over AssetStore snapshots. Withdrawn assets are excluded
from results by default so discovery can never hand out revoked handles;
callers that need audit visibility opt in explicitly.
"""

from __future__ import annotations

from schemas.asset import AssetMetadata
from src.records.store import AssetStore


class AssetDiscovery:
    def __init__(self, store: AssetStore) -> None:
        self._store = store

    def search(
        self,
        tenant_id: str,
        action: str | None = None,
        include_withdrawn: bool = False,
    ) -> list[AssetMetadata]:
        results: list[AssetMetadata] = []
        for asset_id in self._store.asset_ids():
            asset = self._store.get(asset_id)
            if asset.tenant_id != tenant_id:
                continue
            if asset.withdrawn and not include_withdrawn:
                continue
            if (
                action is not None
                and asset.usage_constraints
                and action not in asset.usage_constraints
            ):
                continue
            results.append(asset)
        return sorted(results, key=lambda a: a.asset_id)
