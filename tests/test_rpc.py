from __future__ import annotations

import base64
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from corewarden.errors import ConfigurationError, RpcResponseError, RpcTransportError
from corewarden.rpc import CoreRpcNodeAdapter, JsonRpcHttpTransport
from corewarden.tools import create_diagnostic_tools


class FakeTransport:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def call(self, method: str) -> Any:
        self.calls.append(method)
        return self.responses[method]


class FakeHttpResponse:
    def __init__(self, document: Any, *, content_length: int | str | None = None) -> None:
        self.body = json.dumps(document).encode()
        self.offset = 0
        self.read_calls = 0
        self.headers = (
            {} if content_length is None else {"Content-Length": str(content_length)}
        )

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self.read1(amount)

    def read1(self, amount: int = -1) -> bytes:
        self.read_calls += 1
        if amount < 0:
            amount = len(self.body) - self.offset
        chunk = self.body[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk


@contextmanager
def running_http_server(
    handler: type[BaseHTTPRequestHandler],
) -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


@pytest.fixture
def rpc_responses() -> dict[str, Any]:
    return {
        "getblockchaininfo": {
            "chain": "custom-main",
            "blocks": 1250,
            "headers": 1250,
            "verificationprogress": 0.99999,
            "initialblockdownload": False,
            "warnings": "",
            "unexpected_secret": "FAKE_SECRET_ADAPTER_MUST_DROP",
        },
        "getnetworkinfo": {
            "subversion": "/IdentifyingNode:1.0/",
            "networkactive": True,
            "connections": 4,
            "connections_in": 1,
            "connections_out": 3,
            "localservices": "0000000000000409",
            "localservicesnames": ["NETWORK", "WITNESS"],
            "localaddresses": [{"address": "203.0.113.50", "port": 8338}],
            "networks": [
                {
                    "name": "ipv4",
                    "limited": False,
                    "reachable": True,
                    "proxy": "127.0.0.1:9050",
                }
            ],
            "warnings": "",
        },
        "getpeerinfo": [
            {
                "id": 1,
                "addr": "192.0.2.10:8338",
                "addrbind": "127.0.0.1:50000",
                "addrlocal": "198.51.100.20:8338",
                "mapped_as": 64500,
                "subver": "/Identifying:1.0/",
                "inbound": False,
                "connection_type": "outbound-full-relay",
                "services": "0000000000000409",
                "servicesnames": ["NETWORK", "WITNESS"],
                "startingheight": 1250,
                "synced_headers": 1250,
                "synced_blocks": 1250,
                "pingtime": 0.02,
                "minping": 0.01,
                "conntime": 1700000000,
                "lastsend": 1700000100,
                "lastrecv": 1700000101,
                "bytessent": 1000,
                "bytesrecv": 2000,
                "inflight": [1249],
            }
        ],
        "getchaintips": [
            {
                "height": 1250,
                "branchlen": 0,
                "status": "active",
                "hash": "FAKE_SECRET_ADAPTER_MUST_DROP",
            }
        ],
    }


def test_adapter_maps_only_expected_core_rpc_methods(rpc_responses: dict[str, Any]) -> None:
    transport = FakeTransport(rpc_responses)
    node = CoreRpcNodeAdapter(transport)

    blockchain = node.get_blockchain_status()
    assert blockchain["blocks"] == 1250
    assert set(blockchain) == {
        "blocks",
        "headers",
        "verificationprogress",
        "initialblockdownload",
    }
    network = node.get_network_status()
    assert network["connections"] == 4
    assert network["localservicesnames"] == ["NETWORK", "WITNESS"]
    assert network["networks"] == [{"name": "ipv4", "limited": False, "reachable": True}]
    assert "localaddresses" not in network
    assert "subversion" not in network
    assert "proxy" not in network["networks"][0]
    peer = node.get_peer_information()[0]
    assert peer["synced_blocks"] == 1250
    assert peer["connection_type"] == "outbound-full-relay"
    assert peer["servicesnames"] == ["NETWORK", "WITNESS"]
    assert "addr" not in peer
    assert "addrbind" not in peer
    assert "addrlocal" not in peer
    assert "id" not in peer
    assert "mapped_as" not in peer
    assert "subver" not in peer
    assert node.get_chain_tips() == [{"height": 1250, "branchlen": 0, "status": "active"}]
    assert transport.calls == [
        "getblockchaininfo",
        "getnetworkinfo",
        "getpeerinfo",
        "getchaintips",
    ]


def test_peer_tool_payload_cannot_contain_raw_identifiers(
    rpc_responses: dict[str, Any],
) -> None:
    raw_peer = rpc_responses["getpeerinfo"][0]
    raw_peer["hostname"] = "peer.example.invalid"
    raw_peer["session_id"] = "stable-node-identifier"
    raw_peer["connection_type"] = "198.51.100.99:8338"
    node = CoreRpcNodeAdapter(FakeTransport(rpc_responses))

    tools = create_diagnostic_tools(node)
    network_payload = tools[1]._tool_func()
    peer_tool = tools[2]
    payload = peer_tool._tool_func()
    serialized = json.dumps({"network": network_payload, "peers": payload})

    assert payload[0]["synced_blocks"] == 1250
    assert payload[0]["pingtime"] == 0.02
    assert "connection_type" not in payload[0]
    assert "192.0.2.10" not in serialized
    assert "198.51.100.99" not in serialized
    assert "peer.example.invalid" not in serialized
    assert "stable-node-identifier" not in serialized
    assert "Identifying" not in serialized
    assert "203.0.113.50" not in serialized
    assert "127.0.0.1:9050" not in serialized


def test_adapter_rejects_methods_outside_read_only_allow_list() -> None:
    node = CoreRpcNodeAdapter(FakeTransport({}))

    with pytest.raises(ValueError, match="read-only allow-list"):
        node._call("sendtoaddress")


def test_adapter_rejects_unexpected_result_shapes() -> None:
    node = CoreRpcNodeAdapter(FakeTransport({"getpeerinfo": {"not": "a list"}}))

    with pytest.raises(RpcTransportError, match="list of objects"):
        node.get_peer_information()


def test_http_transport_sends_json_rpc_and_basic_auth() -> None:
    response = FakeHttpResponse({"result": {"blocks": 1}, "error": None, "id": "corewarden"})
    transport = JsonRpcHttpTransport(
        "http://127.0.0.1:8332", username="observer", password="secret", timeout_seconds=3
    )

    with patch("corewarden.rpc._DIRECT_OPENER.open", return_value=response) as mocked_open:
        result = transport.call("getblockchaininfo")

    request = mocked_open.call_args.args[0]
    sent = json.loads(request.data)
    expected_token = base64.b64encode(b"observer:secret").decode()
    assert sent == {
        "jsonrpc": "1.0",
        "id": "corewarden",
        "method": "getblockchaininfo",
        "params": [],
    }
    assert request.get_header("Authorization") == f"Basic {expected_token}"
    assert mocked_open.call_args.kwargs["timeout"] <= 3
    assert mocked_open.call_args.kwargs["timeout"] > 0
    assert result == {"blocks": 1}


def test_http_transport_rejects_disallowed_method_before_opening_connection() -> None:
    transport = JsonRpcHttpTransport("http://127.0.0.1:8332")

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open") as mocked_open,
        pytest.raises(ValueError, match="outside CoreWarden's read-only allow-list"),
    ):
        transport.call("sendtoaddress")

    mocked_open.assert_not_called()


def test_http_transport_raises_safe_rpc_error() -> None:
    response = FakeHttpResponse(
        {"result": None, "error": {"code": -32601, "message": "Method not found"}}
    )
    transport = JsonRpcHttpTransport("http://127.0.0.1:8332")

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open", return_value=response),
        pytest.raises(RpcResponseError) as caught,
    ):
        transport.call("getchaintips")

    assert caught.value.code == -32601
    assert caught.value.method == "getchaintips"
    assert "Method not found" not in str(caught.value)


def test_http_transport_hides_credentials_on_http_failure() -> None:
    error = HTTPError("http://node", 401, "Unauthorized", hdrs=None, fp=None)
    transport = JsonRpcHttpTransport(
        "http://127.0.0.1:8332", username="observer", password="do-not-leak"
    )

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open", side_effect=error),
        pytest.raises(RpcTransportError) as caught,
    ):
        transport.call("getnetworkinfo")

    assert "do-not-leak" not in str(caught.value)
    assert "HTTP 401" in str(caught.value)


def test_http_transport_rejects_invalid_json() -> None:
    response = FakeHttpResponse({})
    response.body = b"not-json"
    transport = JsonRpcHttpTransport("http://node")

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open", return_value=response),
        pytest.raises(RpcTransportError, match="invalid JSON"),
    ):
        transport.call("getnetworkinfo")


def test_http_transport_rejects_remote_plaintext_credentials() -> None:
    with pytest.raises(ConfigurationError, match="must use https://"):
        JsonRpcHttpTransport(
            "http://node.example.invalid:8332",
            username="observer",
            password="fake-rpc-password",
        )


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf"), True])
def test_http_transport_rejects_invalid_deadlines(timeout: float) -> None:
    with pytest.raises(ConfigurationError, match="timeout must be greater"):
        JsonRpcHttpTransport("http://127.0.0.1:8332", timeout_seconds=timeout)


def test_http_transport_bypasses_environment_proxy(monkeypatch: Any) -> None:
    class TargetHandler(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self) -> None:
            type(self).calls += 1
            body = b'{"result":{"blocks":1},"error":null,"id":"corewarden"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            return None

    class ProxyHandler(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self) -> None:
            type(self).calls += 1
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args: Any) -> None:
            return None

    with running_http_server(TargetHandler) as target, running_http_server(
        ProxyHandler
    ) as proxy:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("http_proxy", proxy_url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        transport = JsonRpcHttpTransport(
            f"http://127.0.0.1:{target.server_port}",
            username="observer",
            password="fake-rpc-password",
        )

        with patch("urllib.request.proxy_bypass", return_value=False):
            result = transport.call("getblockchaininfo")

    assert result == {"blocks": 1}
    assert TargetHandler.calls == 1
    assert ProxyHandler.calls == 0


def test_http_transport_rejects_redirect_without_contacting_target() -> None:
    class RedirectTargetHandler(BaseHTTPRequestHandler):
        calls = 0

        def do_GET(self) -> None:
            type(self).calls += 1
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *_args: Any) -> None:
            return None

    class RedirectSourceHandler(BaseHTTPRequestHandler):
        target_url = ""

        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", type(self).target_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args: Any) -> None:
            return None

    with running_http_server(RedirectTargetHandler) as target, running_http_server(
        RedirectSourceHandler
    ) as source:
        RedirectSourceHandler.target_url = f"http://127.0.0.1:{target.server_port}/capture"
        transport = JsonRpcHttpTransport(
            f"http://127.0.0.1:{source.server_port}",
            username="observer",
            password="fake-rpc-password",
        )

        with pytest.raises(RpcTransportError, match="redirect rejected"):
            transport.call("getblockchaininfo")

    assert RedirectTargetHandler.calls == 0


def test_http_transport_rejects_declared_oversized_response_before_reading() -> None:
    response = FakeHttpResponse({"result": {}}, content_length=33)
    transport = JsonRpcHttpTransport("http://127.0.0.1:8332", max_response_bytes=32)

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open", return_value=response),
        pytest.raises(RpcTransportError, match="32-byte safety limit"),
    ):
        transport.call("getblockchaininfo")

    assert response.read_calls == 0


def test_http_transport_rejects_invalid_content_length_before_reading() -> None:
    response = FakeHttpResponse({"result": {}}, content_length="not-a-length")
    transport = JsonRpcHttpTransport("http://127.0.0.1:8332", max_response_bytes=32)

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open", return_value=response),
        pytest.raises(RpcTransportError, match="invalid headers"),
    ):
        transport.call("getblockchaininfo")

    assert response.read_calls == 0


def test_http_transport_rejects_streamed_oversized_response_at_limit_plus_one() -> None:
    response = FakeHttpResponse({})
    response.body = b"x" * 33
    transport = JsonRpcHttpTransport("http://127.0.0.1:8332", max_response_bytes=32)

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open", return_value=response),
        pytest.raises(RpcTransportError, match="32-byte safety limit"),
    ):
        transport.call("getblockchaininfo")

    assert response.offset == 33


def test_http_transport_rejects_oversized_chunked_response() -> None:
    class ChunkedHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = b"x" * 33
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(f"{len(body):x}\r\n".encode("ascii"))
            self.wfile.write(body + b"\r\n0\r\n\r\n")

        def log_message(self, *_args: Any) -> None:
            return None

    with running_http_server(ChunkedHandler) as server:
        transport = JsonRpcHttpTransport(
            f"http://127.0.0.1:{server.server_port}", max_response_bytes=32
        )

        with pytest.raises(RpcTransportError, match="32-byte safety limit"):
            transport.call("getblockchaininfo")


def test_http_transport_rejects_response_that_exhausts_total_deadline() -> None:
    response = FakeHttpResponse({"result": {}, "error": None})
    transport = JsonRpcHttpTransport("http://127.0.0.1:8332", timeout_seconds=1)

    with (
        patch("corewarden.rpc._DIRECT_OPENER.open", return_value=response),
        patch("corewarden.rpc.monotonic", side_effect=[0.0, 0.0, 0.0, 1.1]),
        pytest.raises(RpcTransportError, match="timed out"),
    ):
        transport.call("getblockchaininfo")

    assert response.read_calls == 1
