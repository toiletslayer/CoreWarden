"""The only node capabilities exposed to the Strands agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from strands import tool

from corewarden.node import CoreNode


def create_diagnostic_tools(node: CoreNode) -> list[Callable[..., Any]]:
    """Bind the four fixed read-only node methods as Strands function tools."""

    @tool
    def get_blockchain_status() -> dict[str, Any]:
        """Read local chain height, header height, sync progress, chainwork, and warnings."""
        return dict(node.get_blockchain_status())

    @tool
    def get_network_status() -> dict[str, Any]:
        """Read network activity, inbound/outbound connection counts, and network warnings."""
        return dict(node.get_network_status())

    @tool
    def get_peer_information() -> list[dict[str, Any]]:
        """Read peer connectivity, direction, services, latency, and peer-reported heights."""
        return [dict(peer) for peer in node.get_peer_information()]

    @tool
    def get_chain_tips() -> list[dict[str, Any]]:
        """Read active, valid-fork, and invalid chain tips known to the local node."""
        return [dict(tip) for tip in node.get_chain_tips()]

    return [
        get_blockchain_status,
        get_network_status,
        get_peer_information,
        get_chain_tips,
    ]
