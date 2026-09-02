"""The only node capabilities exposed to the Strands agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from strands import tool

from corewarden.node import CoreNode

_T = TypeVar("_T")
_SAFE_TOOL_FAILURE = "The fixed read-only node tool failed; treat its evidence as unavailable."


def _safe_read(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except Exception:
        raise RuntimeError(_SAFE_TOOL_FAILURE) from None


def create_diagnostic_tools(node: CoreNode) -> list[Callable[..., Any]]:
    """Bind the four fixed read-only node methods as Strands function tools."""

    @tool
    def get_blockchain_status() -> dict[str, Any]:
        """Read local chain height, header height, sync progress, chainwork, and warnings."""
        return dict(_safe_read(node.get_blockchain_status))

    @tool
    def get_network_status() -> dict[str, Any]:
        """Read network activity, inbound/outbound connection counts, and network warnings."""
        return dict(_safe_read(node.get_network_status))

    @tool
    def get_peer_information() -> list[dict[str, Any]]:
        """Read peer connectivity, direction, services, latency, and peer-reported heights."""
        return [dict(peer) for peer in _safe_read(node.get_peer_information)]

    @tool
    def get_chain_tips() -> list[dict[str, Any]]:
        """Read active, valid-fork, and invalid chain tips known to the local node."""
        return [dict(tip) for tip in _safe_read(node.get_chain_tips)]

    return [
        get_blockchain_status,
        get_network_status,
        get_peer_information,
        get_chain_tips,
    ]
