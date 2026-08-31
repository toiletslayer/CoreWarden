"""Narrow JSON-RPC transport and Core-compatible node adapter."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from corewarden.errors import RpcResponseError, RpcTransportError
from corewarden.node import JsonObject

_PEER_BOOLEAN_FIELDS = frozenset({"inbound", "relaytxes", "addr_relay_enabled"})
_PEER_NUMERIC_FIELDS = frozenset(
    {
        "startingheight",
        "synced_headers",
        "synced_blocks",
        "pingtime",
        "minping",
        "pingwait",
        "conntime",
        "lastsend",
        "lastrecv",
        "last_transaction",
        "last_block",
        "bytessent",
        "bytesrecv",
        "timeoffset",
        "banscore",
        "addr_processed",
        "addr_rate_limited",
    }
)
_PEER_TOKEN_FIELDS = frozenset({"connection_type", "transport_protocol_type", "services"})
_SAFE_PEER_TOKEN = re.compile(r"^[A-Za-z0-9_+-]{1,64}$")


def _is_safe_token(value: Any) -> bool:
    return isinstance(value, str) and bool(_SAFE_PEER_TOKEN.fullmatch(value))


def _project_network_health(network: Mapping[str, Any]) -> dict[str, Any]:
    """Remove local/proxy endpoints while preserving network-health evidence."""
    projected: dict[str, Any] = {}
    for field in {"networkactive", "localrelay"}:
        value = network.get(field)
        if type(value) is bool:
            projected[field] = value
    for field in {"connections", "connections_in", "connections_out", "timeoffset"}:
        value = network.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            projected[field] = value
    for field in {"localservices", "warnings"}:
        value = network.get(field)
        if isinstance(value, str):
            projected[field] = value

    services = network.get("localservicesnames")
    if isinstance(services, list) and all(_is_safe_token(item) for item in services):
        projected["localservicesnames"] = list(services)

    networks = network.get("networks")
    if isinstance(networks, list):
        safe_networks = []
        for item in networks:
            if not isinstance(item, Mapping) or not _is_safe_token(item.get("name")):
                continue
            safe_item = {"name": item["name"]}
            for field in {"limited", "reachable"}:
                if type(item.get(field)) is bool:
                    safe_item[field] = item[field]
            safe_networks.append(safe_item)
        projected["networks"] = safe_networks
    return projected


def _project_peer_health(peer: Mapping[str, Any]) -> dict[str, Any]:
    """Return only non-identifying fields needed for peer-health reasoning."""
    projected: dict[str, Any] = {}
    for field in _PEER_BOOLEAN_FIELDS:
        value = peer.get(field)
        if type(value) is bool:
            projected[field] = value
    for field in _PEER_NUMERIC_FIELDS:
        value = peer.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            projected[field] = value
    for field in _PEER_TOKEN_FIELDS:
        value = peer.get(field)
        is_numeric_token = isinstance(value, int) and not isinstance(value, bool)
        if is_numeric_token or _is_safe_token(value):
            projected[field] = value

    services = peer.get("servicesnames")
    if isinstance(services, list) and all(_is_safe_token(item) for item in services):
        projected["servicesnames"] = list(services)

    inflight = peer.get("inflight")
    if isinstance(inflight, list) and all(
        isinstance(item, int) and not isinstance(item, bool) for item in inflight
    ):
        projected["inflight"] = list(inflight)
    return projected


class RpcTransport(Protocol):
    def call(self, method: str) -> Any:
        """Call one parameterless RPC method."""


@dataclass(slots=True)
class JsonRpcHttpTransport:
    url: str
    username: str | None = None
    password: str | None = None
    timeout_seconds: float = 10.0

    def call(self, method: str) -> Any:
        payload = json.dumps(
            {"jsonrpc": "1.0", "id": "corewarden", "method": method, "params": []}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.username is not None and self.password is not None:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        request = Request(self.url, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                body = response.read()
        except HTTPError as exc:
            raise RpcTransportError(
                f"RPC endpoint returned HTTP {exc.code} while calling {method!r}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RpcTransportError(f"RPC endpoint unavailable while calling {method!r}") from exc

        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RpcTransportError(
                f"RPC endpoint returned invalid JSON while calling {method!r}"
            ) from exc
        if not isinstance(document, dict):
            raise RpcTransportError(f"RPC response for {method!r} was not an object")

        error = document.get("error")
        if error:
            if isinstance(error, Mapping):
                code = error.get("code")
                message = str(error.get("message", "unknown RPC error"))
            else:
                code = None
                message = str(error)
            raise RpcResponseError(method, code if isinstance(code, int) else None, message)
        if "result" not in document:
            raise RpcTransportError(f"RPC response for {method!r} omitted result")
        return document["result"]


class CoreRpcNodeAdapter:
    """Adapter for nodes implementing the Bitcoin Core-style read RPC surface."""

    _ALLOWED_METHODS = frozenset(
        {"getblockchaininfo", "getnetworkinfo", "getpeerinfo", "getchaintips"}
    )

    def __init__(self, transport: RpcTransport) -> None:
        self._transport = transport

    def _call(self, method: str) -> Any:
        if method not in self._ALLOWED_METHODS:
            raise ValueError(f"RPC method {method!r} is outside CoreWarden's read-only allow-list")
        return self._transport.call(method)

    def _object(self, method: str) -> JsonObject:
        result = self._call(method)
        if not isinstance(result, Mapping):
            raise RpcTransportError(f"RPC result for {method!r} was not an object")
        return cast(JsonObject, result)

    def _objects(self, method: str) -> Sequence[JsonObject]:
        result = self._call(method)
        if not isinstance(result, list) or not all(isinstance(item, Mapping) for item in result):
            raise RpcTransportError(f"RPC result for {method!r} was not a list of objects")
        return cast(Sequence[JsonObject], result)

    def get_blockchain_status(self) -> JsonObject:
        return self._object("getblockchaininfo")

    def get_network_status(self) -> JsonObject:
        return _project_network_health(self._object("getnetworkinfo"))

    def get_peer_information(self) -> Sequence[JsonObject]:
        return [_project_peer_health(peer) for peer in self._objects("getpeerinfo")]

    def get_chain_tips(self) -> Sequence[JsonObject]:
        return self._objects("getchaintips")
