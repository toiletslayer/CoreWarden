"""Strict, bounded projections for every provider-visible node observation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

MAX_PEER_OBSERVATIONS = 256
MAX_CHAIN_TIPS = 64
MAX_NETWORKS = 16
MAX_SERVICE_NAMES = 32
MAX_INFLIGHT_BLOCKS = 64
MAX_TOKEN_CHARACTERS = 64

WARNING_PRESENT = "Node reported a warning; untrusted warning text omitted."

_MAX_SIGNED_INTEGER = 2**63 - 1
_CHAIN_NAMES = frozenset({"main", "test", "testnet", "testnet4", "signet", "regtest"})
_NETWORK_NAMES = frozenset({"ipv4", "ipv6", "onion", "i2p", "cjdns"})
_SERVICE_NAMES = frozenset(
    {
        "NETWORK",
        "GETUTXO",
        "BLOOM",
        "WITNESS",
        "COMPACT_FILTERS",
        "NETWORK_LIMITED",
        "P2P_V2",
    }
)
_CONNECTION_TYPES = frozenset(
    {
        "inbound",
        "outbound-full-relay",
        "block-relay-only",
        "manual",
        "addr-fetch",
        "feeler",
    }
)
_TRANSPORT_PROTOCOL_TYPES = frozenset({"v1", "v2"})
_CHAIN_TIP_STATUSES = frozenset(
    {"active", "valid-fork", "valid-headers", "headers-only", "invalid"}
)

_BLOCKCHAIN_BOOLEAN_FIELDS = frozenset(
    {"initialblockdownload", "pruned", "automatic_pruning"}
)
_BLOCKCHAIN_INTEGER_FIELDS = frozenset(
    {"blocks", "headers", "time", "mediantime", "size_on_disk", "pruneheight", "prune_target_size"}
)
_PEER_BOOLEAN_FIELDS = frozenset({"inbound", "relaytxes", "addr_relay_enabled"})
_PEER_INTEGER_FIELDS = frozenset(
    {
        "startingheight",
        "synced_headers",
        "synced_blocks",
        "conntime",
        "lastsend",
        "lastrecv",
        "last_transaction",
        "last_block",
        "bytessent",
        "bytesrecv",
        "banscore",
        "addr_processed",
        "addr_rate_limited",
    }
)
_PEER_NUMBER_FIELDS = frozenset({"pingtime", "minping", "pingwait"})


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _boolean(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _integer(
    value: Any, *, minimum: int = 0, maximum: int = _MAX_SIGNED_INTEGER
) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value if minimum <= value <= maximum else None


def _number(
    value: Any, *, minimum: float = 0.0, maximum: float = 1e100
) -> int | float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if minimum <= value <= maximum else None


def _enumeration(value: Any, allowed: frozenset[str]) -> str | None:
    if not isinstance(value, str) or len(value) > MAX_TOKEN_CHARACTERS:
        return None
    return value if value in allowed else None


def _warning_marker(value: Any) -> str | None:
    """Represent arbitrary warning content without forwarding any of its text."""
    if isinstance(value, str):
        if not value:
            return None
        if len(value) > MAX_TOKEN_CHARACTERS:
            return WARNING_PRESENT
        sanitized = "".join(
            character if character.isprintable() and character != "\x7f" else " "
            for character in value
        )
        return WARNING_PRESENT if sanitized.strip() else None
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return WARNING_PRESENT if value else None
    return None


def _project_enum_list(value: Any, allowed: frozenset[str], maximum: int) -> list[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return None
    projected: list[str] = []
    for item in value[:maximum]:
        safe = _enumeration(item, allowed)
        if safe is not None:
            projected.append(safe)
    return projected


def project_blockchain_status(value: Any) -> dict[str, Any]:
    """Project getblockchaininfo into the exact provider-visible schema."""
    source = _mapping(value)
    projected: dict[str, Any] = {}

    chain = _enumeration(source.get("chain"), _CHAIN_NAMES)
    if chain is not None:
        projected["chain"] = chain
    for field in _BLOCKCHAIN_BOOLEAN_FIELDS:
        safe = _boolean(source.get(field))
        if safe is not None:
            projected[field] = safe
    for field in _BLOCKCHAIN_INTEGER_FIELDS:
        safe = _integer(source.get(field))
        if safe is not None:
            projected[field] = safe
    difficulty = _number(source.get("difficulty"))
    if difficulty is not None:
        projected["difficulty"] = difficulty
    progress = _number(source.get("verificationprogress"), maximum=1.0)
    if progress is not None:
        projected["verificationprogress"] = progress
    warning = _warning_marker(source.get("warnings"))
    if warning is not None:
        projected["warnings"] = warning
    return projected


def project_network_status(value: Any) -> dict[str, Any]:
    """Project getnetworkinfo without endpoints, identities, or free-form strings."""
    source = _mapping(value)
    projected: dict[str, Any] = {}
    for field in {"networkactive", "localrelay"}:
        safe = _boolean(source.get(field))
        if safe is not None:
            projected[field] = safe
    for field in {"connections", "connections_in", "connections_out"}:
        safe = _integer(source.get(field), maximum=1_000_000)
        if safe is not None:
            projected[field] = safe
    time_offset = _integer(
        source.get("timeoffset"), minimum=-_MAX_SIGNED_INTEGER, maximum=_MAX_SIGNED_INTEGER
    )
    if time_offset is not None:
        projected["timeoffset"] = time_offset
    service_names = _project_enum_list(
        source.get("localservicesnames"), _SERVICE_NAMES, MAX_SERVICE_NAMES
    )
    if service_names is not None:
        projected["localservicesnames"] = service_names

    networks = source.get("networks")
    if isinstance(networks, Sequence) and not isinstance(networks, str | bytes | bytearray):
        safe_networks = []
        for item in networks[:MAX_NETWORKS]:
            network = _mapping(item)
            name = _enumeration(network.get("name"), _NETWORK_NAMES)
            if name is None:
                continue
            safe_item: dict[str, Any] = {"name": name}
            for field in {"limited", "reachable"}:
                safe = _boolean(network.get(field))
                if safe is not None:
                    safe_item[field] = safe
            safe_networks.append(safe_item)
        projected["networks"] = safe_networks

    warning = _warning_marker(source.get("warnings"))
    if warning is not None:
        projected["warnings"] = warning
    return projected


def _project_peer(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    projected: dict[str, Any] = {}
    for field in _PEER_BOOLEAN_FIELDS:
        safe = _boolean(source.get(field))
        if safe is not None:
            projected[field] = safe
    for field in _PEER_INTEGER_FIELDS:
        minimum = -1 if field in {"startingheight", "synced_headers", "synced_blocks"} else 0
        safe = _integer(source.get(field), minimum=minimum)
        if safe is not None:
            projected[field] = safe
    time_offset = _integer(
        source.get("timeoffset"), minimum=-_MAX_SIGNED_INTEGER, maximum=_MAX_SIGNED_INTEGER
    )
    if time_offset is not None:
        projected["timeoffset"] = time_offset
    for field in _PEER_NUMBER_FIELDS:
        safe = _number(source.get(field), maximum=1_000_000_000)
        if safe is not None:
            projected[field] = safe

    connection_type = _enumeration(source.get("connection_type"), _CONNECTION_TYPES)
    if connection_type is not None:
        projected["connection_type"] = connection_type
    transport_type = _enumeration(
        source.get("transport_protocol_type"), _TRANSPORT_PROTOCOL_TYPES
    )
    if transport_type is not None:
        projected["transport_protocol_type"] = transport_type
    service_names = _project_enum_list(
        source.get("servicesnames"), _SERVICE_NAMES, MAX_SERVICE_NAMES
    )
    if service_names is not None:
        projected["servicesnames"] = service_names

    inflight = source.get("inflight")
    if isinstance(inflight, Sequence) and not isinstance(inflight, str | bytes | bytearray):
        projected["inflight"] = [
            safe
            for item in inflight[:MAX_INFLIGHT_BLOCKS]
            for safe in (_integer(item),)
            if safe is not None
        ]
    return projected


def project_peer_information(value: Any) -> list[dict[str, Any]]:
    """Project a bounded prefix of getpeerinfo into exact peer-health fields."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [
        _project_peer(peer)
        for peer in value[:MAX_PEER_OBSERVATIONS]
        if isinstance(peer, Mapping)
    ]


def project_chain_tips(value: Any) -> list[dict[str, Any]]:
    """Project a bounded prefix of getchaintips into exact fork-health fields."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    projected = []
    for item in value[:MAX_CHAIN_TIPS]:
        tip = _mapping(item)
        if not tip:
            continue
        safe_tip: dict[str, Any] = {}
        height = _integer(tip.get("height"))
        if height is not None:
            safe_tip["height"] = height
        branch_length = _integer(tip.get("branchlen"))
        if branch_length is not None:
            safe_tip["branchlen"] = branch_length
        status = _enumeration(tip.get("status"), _CHAIN_TIP_STATUSES)
        if status is not None:
            safe_tip["status"] = status
        projected.append(safe_tip)
    return projected


def project_tool_result(tool_name: str, value: Any) -> dict[str, Any] | list[dict[str, Any]]:
    """Apply the correct exact schema at a provider's fixed tool boundary."""
    projectors = {
        "get_blockchain_status": project_blockchain_status,
        "get_network_status": project_network_status,
        "get_peer_information": project_peer_information,
        "get_chain_tips": project_chain_tips,
    }
    projector = projectors.get(tool_name)
    if projector is None:
        raise ValueError("Tool name is outside the observation allow-list")
    return projector(value)
