"""External connector registry with quarantine propagation.

Connectors cache retrieval handles for assets. On withdrawal the asset store
invokes quarantine() synchronously; connectors flip to Quarantined and refuse
 reads until an explicit acknowledge() after revalidation. No background
polling is involved, which bounds revocation-to-block latency.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum


class ConnectorState(str, Enum):
    ACTIVE = "Active"
    QUARANTINED = "Quarantined"
    ACKNOWLEDGED = "Acknowledged"


@dataclass
class Connector:
    connector_id: str
    state: ConnectorState = ConnectorState.ACTIVE
    quarantined_assets: set[str] = field(default_factory=set)
    last_revocation_ns: int | None = None

    def can_serve(self, asset_id: str) -> bool:
        return self.state == ConnectorState.ACTIVE and asset_id not in self.quarantined_assets


class ConnectorRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._connectors: dict[str, Connector] = {}

    def register(self, connector_id: str) -> Connector:
        with self._lock:
            if connector_id in self._connectors:
                raise ValueError(f"connector {connector_id!r} already registered")
            connector = Connector(connector_id=connector_id)
            self._connectors[connector_id] = connector
            return connector

    def get(self, connector_id: str) -> Connector:
        with self._lock:
            return self._connectors[connector_id]

    def connector_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._connectors.keys())

    def quarantine(self, asset_id: str, revoked_ns: int) -> None:
        with self._lock:
            for connector in self._connectors.values():
                connector.state = ConnectorState.QUARANTINED
                connector.quarantined_assets.add(asset_id)
                connector.last_revocation_ns = revoked_ns

    def acknowledge(self, connector_id: str) -> Connector:
        with self._lock:
            connector = self._connectors[connector_id]
            connector.state = ConnectorState.ACKNOWLEDGED
            connector.quarantined_assets.clear()
            return connector

    def reactivate(self, connector_id: str) -> Connector:
        with self._lock:
            connector = self._connectors[connector_id]
            if connector.quarantined_assets:
                raise ValueError("cannot reactivate with pending quarantined assets")
            connector.state = ConnectorState.ACTIVE
            return connector
