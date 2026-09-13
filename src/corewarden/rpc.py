"""Narrow JSON-RPC transport and Core-compatible node adapter."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from corewarden.config import require_safe_rpc_auth_transport, validate_rpc_endpoint_url
from corewarden.errors import ConfigurationError, RpcResponseError, RpcTransportError
from corewarden.node import JsonObject
from corewarden.observations import (
    project_blockchain_status,
    project_chain_tips,
    project_network_status,
    project_peer_information,
)

RPC_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
_RESPONSE_READ_CHUNK_BYTES = 64 * 1024
ALLOWED_RPC_METHODS = frozenset(
    {"getblockchaininfo", "getnetworkinfo", "getpeerinfo", "getchaintips"}
)


class RpcTransport(Protocol):
    def call(self, method: str) -> Any:
        """Call one parameterless RPC method."""


class _RejectRedirects(HTTPRedirectHandler):
    """Turn every redirect into an HTTP error before a second request is built."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        del new_url
        raise HTTPError(request.full_url, code, message, headers, file_pointer)


_DIRECT_OPENER = build_opener(ProxyHandler({}), _RejectRedirects())


def _content_length(response: Any, method: str) -> int | None:
    headers = getattr(response, "headers", None)
    raw_value = headers.get("Content-Length") if headers is not None else None
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise RpcTransportError(
            f"RPC endpoint returned invalid headers while calling {method!r}"
        ) from None
    if value < 0:
        raise RpcTransportError(f"RPC endpoint returned invalid headers while calling {method!r}")
    return value


def _deadline_remaining(deadline: float, method: str) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise RpcTransportError(f"RPC endpoint timed out while calling {method!r}")
    return remaining


def _read_bounded_body(
    response: Any, method: str, *, maximum_bytes: int, deadline: float
) -> bytes:
    declared_length = _content_length(response, method)
    if declared_length is not None and declared_length > maximum_bytes:
        raise RpcTransportError(
            f"RPC response exceeded the {maximum_bytes}-byte safety limit "
            f"while calling {method!r}"
        )

    body = bytearray()
    read = getattr(response, "read1", response.read)
    while True:
        _deadline_remaining(deadline, method)
        remaining_capacity = maximum_bytes + 1 - len(body)
        chunk = read(min(_RESPONSE_READ_CHUNK_BYTES, remaining_capacity))
        _deadline_remaining(deadline, method)
        if not isinstance(chunk, bytes | bytearray):
            raise RpcTransportError(
                f"RPC endpoint returned an invalid body while calling {method!r}"
            )
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise RpcTransportError(
                f"RPC response exceeded the {maximum_bytes}-byte safety limit "
                f"while calling {method!r}"
            )


@dataclass(slots=True)
class JsonRpcHttpTransport:
    """Direct, no-redirect JSON-RPC transport with a bounded response body.

    The configured timeout is also treated as a total request deadline. urllib's
    socket timeout cannot asynchronously cancel an in-progress low-level read;
    using ``read1`` where available and checking around every bounded read limits
    that residual overrun, and an overdue response is rejected before JSON parsing.
    """

    url: str
    username: str | None = None
    password: str | None = None
    timeout_seconds: float = 10.0
    max_response_bytes: int = RPC_RESPONSE_MAX_BYTES

    def __post_init__(self) -> None:
        # Settings validates application input; repeat the critical credential
        # policy here so direct transport construction cannot bypass it.
        validate_rpc_endpoint_url(self.url)
        if (self.username is None) != (self.password is None):
            raise ConfigurationError("RPC username and password must be set together")
        if self.username is not None:
            require_safe_rpc_auth_transport(self.url)
        if (
            not isinstance(self.timeout_seconds, int | float)
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ConfigurationError("RPC timeout must be greater than zero")
        if (
            not isinstance(self.max_response_bytes, int)
            or isinstance(self.max_response_bytes, bool)
            or self.max_response_bytes < 1
        ):
            raise ConfigurationError("RPC response safety limit must be at least one byte")

    def call(self, method: str) -> Any:
        if method not in ALLOWED_RPC_METHODS:
            raise ValueError(f"RPC method {method!r} is outside CoreWarden's read-only allow-list")
        payload = json.dumps(
            {"jsonrpc": "1.0", "id": "corewarden", "method": method, "params": []}
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.username is not None and self.password is not None:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        request = Request(self.url, data=payload, headers=headers, method="POST")
        deadline = monotonic() + self.timeout_seconds
        try:
            response = _DIRECT_OPENER.open(
                request, timeout=_deadline_remaining(deadline, method)
            )  # noqa: S310
            with closing(response):
                body = _read_bounded_body(
                    response,
                    method,
                    maximum_bytes=self.max_response_bytes,
                    deadline=deadline,
                )
        except HTTPError as exc:
            exc.close()
            if 300 <= exc.code < 400:
                raise RpcTransportError(
                    f"RPC endpoint redirect rejected while calling {method!r}"
                ) from exc
            raise RpcTransportError(
                f"RPC endpoint returned HTTP {exc.code} while calling {method!r}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RpcTransportError(f"RPC endpoint unavailable while calling {method!r}") from exc

        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise RpcTransportError(
                f"RPC endpoint returned invalid JSON while calling {method!r}"
            ) from exc
        _deadline_remaining(deadline, method)
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

    _ALLOWED_METHODS = ALLOWED_RPC_METHODS

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

    def _list(self, method: str) -> list[Any]:
        result = self._call(method)
        if not isinstance(result, list):
            raise RpcTransportError(f"RPC result for {method!r} was not a list of objects")
        return result

    def get_blockchain_status(self) -> JsonObject:
        return project_blockchain_status(self._object("getblockchaininfo"))

    def get_network_status(self) -> JsonObject:
        return project_network_status(self._object("getnetworkinfo"))

    def get_peer_information(self) -> Sequence[JsonObject]:
        return project_peer_information(self._list("getpeerinfo"))

    def get_chain_tips(self) -> Sequence[JsonObject]:
        return project_chain_tips(self._list("getchaintips"))
