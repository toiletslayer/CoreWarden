from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from corewarden.errors import RpcResponseError, RpcTransportError
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
    def __init__(self, document: Any) -> None:
        self.body = json.dumps(document).encode()

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.body


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
        "getchaintips": [{"height": 1250, "branchlen": 0, "status": "active"}],
    }


def test_adapter_maps_only_expected_core_rpc_methods(rpc_responses: dict[str, Any]) -> None:
    transport = FakeTransport(rpc_responses)
    node = CoreRpcNodeAdapter(transport)

    assert node.get_blockchain_status()["blocks"] == 1250
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
    assert node.get_chain_tips()[0]["status"] == "active"
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

    with patch("corewarden.rpc.urlopen", return_value=response) as mocked_urlopen:
        result = transport.call("getblockchaininfo")

    request = mocked_urlopen.call_args.args[0]
    sent = json.loads(request.data)
    expected_token = base64.b64encode(b"observer:secret").decode()
    assert sent == {
        "jsonrpc": "1.0",
        "id": "corewarden",
        "method": "getblockchaininfo",
        "params": [],
    }
    assert request.get_header("Authorization") == f"Basic {expected_token}"
    assert mocked_urlopen.call_args.kwargs["timeout"] == 3
    assert result == {"blocks": 1}


def test_http_transport_raises_safe_rpc_error() -> None:
    response = FakeHttpResponse(
        {"result": None, "error": {"code": -32601, "message": "Method not found"}}
    )
    transport = JsonRpcHttpTransport("http://127.0.0.1:8332")

    with (
        patch("corewarden.rpc.urlopen", return_value=response),
        pytest.raises(RpcResponseError) as caught,
    ):
        transport.call("getchaintips")

    assert caught.value.code == -32601
    assert caught.value.method == "getchaintips"


def test_http_transport_hides_credentials_on_http_failure() -> None:
    error = HTTPError("http://node", 401, "Unauthorized", hdrs=None, fp=None)
    transport = JsonRpcHttpTransport("http://node", username="observer", password="do-not-leak")

    with (
        patch("corewarden.rpc.urlopen", side_effect=error),
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
        patch("corewarden.rpc.urlopen", return_value=response),
        pytest.raises(RpcTransportError, match="invalid JSON"),
    ):
        transport.call("getnetworkinfo")
