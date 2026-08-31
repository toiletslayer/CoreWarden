"""Generic interface implemented by read-only Core-compatible node adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

JsonObject = Mapping[str, Any]


class CoreNode(Protocol):
    """The complete node capability visible to the diagnostic layer."""

    def get_blockchain_status(self) -> JsonObject:
        """Return chain height, headers, sync state, and chain warnings."""

    def get_network_status(self) -> JsonObject:
        """Return network activity, connection counts, and network warnings."""

    def get_peer_information(self) -> Sequence[JsonObject]:
        """Return current peer observations."""

    def get_chain_tips(self) -> Sequence[JsonObject]:
        """Return known active and competing chain tips."""
