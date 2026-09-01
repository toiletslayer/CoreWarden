from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from corewarden.errors import ProviderError
from corewarden.openai_provider import OPENAI_MODEL, OpenAIResponsesProvider
from corewarden.provider import DiagnosisProvider
from corewarden.rpc import CoreRpcNodeAdapter
from tests.test_agent import FakeNode, sample_diagnosis


@dataclass
class FunctionCall:
    name: str
    call_id: str
    arguments: str = "{}"
    type: str = "function_call"


@dataclass
class FakeResponse:
    output: list[Any]
    output_text: str = ""
    status: str | None = None


class FakeResponses:
    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        return self._responses.pop(0)


@dataclass
class FakeClient:
    responses: Any


def provider_for(responses: FakeResponses, **kwargs: Any) -> OpenAIResponsesProvider:
    return OpenAIResponsesProvider(
        api_key="test-project-key",
        client_factory=lambda api_key: FakeClient(responses),
        **kwargs,
    )


def test_openai_provider_satisfies_protocol_and_invokes_responses_api() -> None:
    expected = sample_diagnosis()
    responses = FakeResponses([FakeResponse([], expected.model_dump_json())])
    provider = provider_for(responses)

    assert isinstance(provider, DiagnosisProvider)
    assert (
        provider.diagnose(
            FakeNode(), system_prompt="safe system", investigation_prompt="investigate"
        )
        == expected
    )

    assert len(responses.calls) == 1
    request = responses.calls[0]
    assert request["model"] == OPENAI_MODEL == "gpt-5.6-luna"
    assert request["instructions"] == "safe system"
    assert request["input"] == [{"role": "user", "content": "investigate"}]
    assert request["store"] is False
    assert request["include"] == ["reasoning.encrypted_content"]
    assert request["service_tier"] == "default"
    assert request["parallel_tool_calls"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert [tool["name"] for tool in request["tools"]] == [
        "get_blockchain_status",
        "get_network_status",
        "get_peer_information",
        "get_chain_tips",
    ]
    output_format = request["text"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    assert set(output_format["schema"]["required"]) == set(output_format["schema"]["properties"])


def test_openai_tool_loop_uses_all_four_sanitized_node_semantics() -> None:
    raw_values = {
        "getblockchaininfo": {"blocks": 10, "headers": 10},
        "getnetworkinfo": {
            "networkactive": True,
            "connections": 1,
            "localaddresses": [{"address": "198.51.100.20", "port": 8338}],
            "subversion": "/IdentifyingNode:1.0/",
            "networks": [
                {
                    "name": "ipv4",
                    "reachable": True,
                    "limited": False,
                    "proxy": "127.0.0.1:9050",
                }
            ],
        },
        "getpeerinfo": [
            {
                "id": 42,
                "addr": "192.0.2.10:8338",
                "addrbind": "127.0.0.1:50000",
                "subver": "/IdentifyingClient:1.0/",
                "mapped_as": 64500,
                "inbound": False,
                "synced_blocks": 10,
            }
        ],
        "getchaintips": [{"height": 10, "branchlen": 0, "status": "active"}],
    }

    class RecordingTransport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def call(self, method: str) -> Any:
            self.calls.append(method)
            return raw_values[method]

    first = FakeResponse(
        [
            FunctionCall("get_blockchain_status", "call-blockchain"),
            FunctionCall("get_network_status", "call-network"),
            FunctionCall("get_peer_information", "call-peers"),
            FunctionCall("get_chain_tips", "call-tips"),
        ]
    )
    responses = FakeResponses([first, FakeResponse([], sample_diagnosis().model_dump_json())])
    transport = RecordingTransport()

    provider_for(responses).diagnose(
        CoreRpcNodeAdapter(transport), system_prompt="system", investigation_prompt="investigate"
    )

    assert transport.calls == [
        "getblockchaininfo",
        "getnetworkinfo",
        "getpeerinfo",
        "getchaintips",
    ]
    tool_outputs = [
        item
        for item in responses.calls[1]["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert [item["call_id"] for item in tool_outputs] == [
        "call-blockchain",
        "call-network",
        "call-peers",
        "call-tips",
    ]
    serialized = json.dumps([json.loads(item["output"]) for item in tool_outputs])
    assert '"synced_blocks": 10' in serialized
    for forbidden in (
        "192.0.2.10",
        "198.51.100.20",
        "127.0.0.1",
        "Identifying",
        "mapped_as",
        '"id":42',
        "localaddresses",
        "proxy",
    ):
        assert forbidden not in serialized


def test_arbitrary_model_tool_name_cannot_reach_node() -> None:
    class GuardNode(FakeNode):
        calls = 0

        def get_blockchain_status(self) -> dict[str, Any]:
            self.calls += 1
            return super().get_blockchain_status()

    node = GuardNode()
    responses = FakeResponses([FakeResponse([FunctionCall("sendtoaddress", "bad-call")])])

    with pytest.raises(ProviderError, match="outside the fixed allow-list"):
        provider_for(responses).diagnose(
            node, system_prompt="system", investigation_prompt="investigate"
        )

    assert node.calls == 0


def test_tool_call_iterations_are_bounded() -> None:
    class LoopingResponses:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> FakeResponse:
            self.calls.append(kwargs)
            return FakeResponse([FunctionCall("get_blockchain_status", f"call-{len(self.calls)}")])

    responses = LoopingResponses()
    provider = OpenAIResponsesProvider(
        api_key="test-project-key",
        max_iterations=2,
        client_factory=lambda api_key: FakeClient(responses),
    )

    with pytest.raises(ProviderError, match=r"iteration limit reached \(2\)"):
        provider.diagnose(FakeNode(), system_prompt="system", investigation_prompt="investigate")

    assert len(responses.calls) == 2


def test_openai_failures_and_provider_repr_do_not_expose_api_key() -> None:
    secret = "sk-project-do-not-leak"

    class FailingResponses:
        def create(self, **kwargs: Any) -> FakeResponse:
            raise RuntimeError(f"provider error included {secret}")

    provider = OpenAIResponsesProvider(
        api_key=secret,
        client_factory=lambda api_key: FakeClient(FailingResponses()),
    )

    with pytest.raises(ProviderError) as caught:
        provider.diagnose(FakeNode(), system_prompt="system", investigation_prompt="investigate")

    assert str(caught.value) == "OpenAI provider invocation failed"
    assert secret not in str(caught.value)
    assert secret not in repr(provider)


def test_openai_provider_rejects_model_override_and_invalid_structured_output() -> None:
    with pytest.raises(ProviderError, match="must be gpt-5.6-luna"):
        OpenAIResponsesProvider(api_key="test-key", model="other-model")

    responses = FakeResponses([FakeResponse([], '{"classification":"healthy"}')])
    with pytest.raises(ProviderError, match="invalid CoreWarden diagnosis"):
        provider_for(responses).diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )


def test_openai_provider_rejects_invalid_configuration_and_client_initialization() -> None:
    with pytest.raises(ProviderError, match="OPENAI_API_KEY is required"):
        OpenAIResponsesProvider(api_key="")
    with pytest.raises(ValueError, match="max_iterations must be at least 1"):
        OpenAIResponsesProvider(api_key="test-key", max_iterations=0)

    provider = OpenAIResponsesProvider(
        api_key="test-key",
        client_factory=lambda api_key: (_ for _ in ()).throw(RuntimeError("unsafe detail")),
    )
    with pytest.raises(ProviderError, match="client initialization failed"):
        provider.diagnose(FakeNode(), system_prompt="system", investigation_prompt="investigate")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ("not-json", "invalid tool arguments"),
        ('{"unexpected":true}', "unsupported tool arguments"),
    ],
)
def test_openai_provider_rejects_invalid_tool_arguments(arguments: str, message: str) -> None:
    responses = FakeResponses(
        [FakeResponse([FunctionCall("get_blockchain_status", "call-1", arguments)])]
    )

    with pytest.raises(ProviderError, match=message):
        provider_for(responses).diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )


def test_openai_tool_failure_is_returned_without_raw_exception_detail() -> None:
    secret = "node-detail-do-not-leak"

    class FailingNode(FakeNode):
        def get_network_status(self) -> dict[str, Any]:
            raise RuntimeError(secret)

    responses = FakeResponses(
        [
            FakeResponse([FunctionCall("get_network_status", "call-network")]),
            FakeResponse([], sample_diagnosis().model_dump_json()),
        ]
    )

    provider_for(responses).diagnose(
        FailingNode(), system_prompt="system", investigation_prompt="investigate"
    )

    tool_output = next(
        item
        for item in responses.calls[1]["input"]
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    )
    serialized = tool_output["output"]
    assert "RuntimeError" in serialized
    assert "read-only node tool failed" in serialized
    assert secret not in serialized


def test_openai_provider_rejects_empty_output_and_mapping_tool_calls() -> None:
    with pytest.raises(ProviderError, match="no validated CoreWarden diagnosis"):
        provider_for(FakeResponses([FakeResponse([])])).diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )

    responses = FakeResponses(
        [
            FakeResponse(
                [
                    {
                        "type": "function_call",
                        "name": "arbitrary_rpc",
                        "call_id": "bad-call",
                        "arguments": "{}",
                    }
                ]
            )
        ]
    )
    with pytest.raises(ProviderError, match="outside the fixed allow-list"):
        provider_for(responses).diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )


def test_openai_configuration_test_is_small_tool_free_and_not_stored() -> None:
    response = FakeResponse([], status="completed")
    responses = FakeResponses([response])

    provider_for(responses).test_configuration()

    request = responses.calls[0]
    assert request == {
        "model": "gpt-5.6-luna",
        "input": "Reply with exactly OK.",
        "reasoning": {"effort": "none"},
        "max_output_tokens": 16,
        "service_tier": "default",
        "store": False,
    }


def test_openai_configuration_test_normalizes_secret_bearing_failure() -> None:
    secret = "sk-configuration-secret"

    class FailingResponses:
        def create(self, **kwargs: Any) -> FakeResponse:
            raise RuntimeError(secret)

    provider = OpenAIResponsesProvider(
        api_key=secret,
        client_factory=lambda api_key: FakeClient(FailingResponses()),
    )

    with pytest.raises(ProviderError) as caught:
        provider.test_configuration()

    assert str(caught.value) == (
        "OpenAI configuration test failed; check the saved key and project access."
    )
    assert secret not in str(caught.value)


def test_openai_configuration_test_rejects_failed_response_status() -> None:
    response = FakeResponse([], status="failed")

    with pytest.raises(ProviderError, match="configuration test failed"):
        provider_for(FakeResponses([response])).test_configuration()
