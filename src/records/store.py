"""Thread-safe asset registry with withdrawal propagation hooks.

Withdrawal is monotonic: once withdrawn, an asset cannot be re-admitted
through this store. Revocation notifies registered listeners (connector
quarantine, token invalidation) synchronously so the gateway blocks on the
very next evaluation without polling delays.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from schemas.asset import AssetMetadata

RevocationListener = Callable[[str, int], None]


class AssetNotFound(Exception):
    pass


class AssetStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._assets: dict[str, AssetMetadata] = {}
        self._revoked_at_monotonic_ns: dict[str, int] = {}
        self._listeners: list[RevocationListener] = []

    def register(self, asset: AssetMetadata) -> None:
        with self._lock:
            if asset.asset_id in self._assets:
                raise ValueError(f"asset {asset.asset_id!r} already registered")
            self._assets[asset.asset_id] = asset

    def get(self, asset_id: str) -> AssetMetadata:
        with self._lock:
            try:
                return self._assets[asset_id]
            except KeyError as exc:
                raise AssetNotFound(asset_id) from exc

    def __contains__(self, asset_id: object) -> bool:
        with self._lock:
            return asset_id in self._assets

    def __len__(self) -> int:
        with self._lock:
            return len(self._assets)

    def asset_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._assets.keys())

    def add_revocation_listener(self, listener: RevocationListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def withdraw(self, asset_id: str) -> AssetMetadata:
        with self._lock:
            current = self._assets.get(asset_id)
            if current is None:
                raise AssetNotFound(asset_id)
            revoked_ns = time.monotonic_ns()
            updated = current.model_copy(update={"withdrawn": True})
            self._assets[asset_id] = updated
            self._revoked_at_monotonic_ns[asset_id] = revoked_ns
            listeners = list(self._listeners)
        for listener in listeners:
            listener(asset_id, revoked_ns)
        return updated

    def revoked_at_monotonic_ns(self, asset_id: str) -> int | None:
        with self._lock:
            return self._revoked_at_monotonic_ns.get(asset_id)

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(timezone.utc)
