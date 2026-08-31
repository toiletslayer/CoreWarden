from __future__ import annotations

from typing import Any

import pytest

from corewarden.agent import INVESTIGATION_PROMPT, SYSTEM_PROMPT, diagnose
from corewarden.models import Classification, Diagnosis, Evidence
from corewarden.rpc import CoreRpcNodeAdapter


class FakeNode:
    def get_blockchain_status(self) -> dict[str, Any]:
        return {"blocks": 10, "headers": 10}

    def get_network_status(self) -> dict[str, Any]:
        return {"networkactive": True, "connections": 2}

    def get_peer_information(self) -> list[dict[str, Any]]:
        return [{"synced_blocks": 10}]

    def get_chain_tips(self) -> list[dict[str, Any]]:
        return [{"height": 10, "branchlen": 0, "status": "active"}]


def sample_diagnosis() -> Diagnosis:
    return Diagnosis(
        classification=Classification.HEALTHY,
        confidence=0.9,
        summary="Consistent observations.",
        evidence=[
            Evidence(
                source="blockchain_status",
                observation="blocks equals headers",
                significance="the node is caught up to known headers",
            ),
            Evidence(
                source="peer_information",
                observation="two peers are connected",
                significance="the node has external connectivity",
            ),
        ],
        uncertainties=[],
        recommended_human_checks=[],
        safety_boundary="Read-only observation; no remediation was performed.",
    )


def test_diagnostic_workflow_uses_provider_abstraction() -> None:
    expected = sample_diagnosis()
    node = FakeNode()
    calls: list[tuple[Any, str, str]] = []

    class CapturingProvider:
        def diagnose(
            self,
            received_node: Any,
            *,
            system_prompt: str,
            investigation_prompt: str,
        ) -> Diagnosis:
            calls.append((received_node, system_prompt, investigation_prompt))
            return expected

    actual = diagnose(node, CapturingProvider())

    assert actual is expected
    assert calls == [(node, SYSTEM_PROMPT, INVESTIGATION_PROMPT)]
    assert "all four" in SYSTEM_PROMPT
    assert "block age alone" in SYSTEM_PROMPT
    assert "Blocks below headers" in SYSTEM_PROMPT
    assert "Missing or sharply degraded peers" in SYSTEM_PROMPT
    assert "network-wide" in SYSTEM_PROMPT


def test_provider_layer_receives_only_adapter_sanitized_rpc_data() -> None:
    class RawTransport:
        def call(self, method: str) -> Any:
            responses = {
                "getpeerinfo": [
                    {
                        "id": 42,
                        "addr": "192.0.2.10:8338",
                        "addrbind": "127.0.0.1:50000",
                        "subver": "/IdentifyingClient:1.0/",
                        "mapped_as": 64500,
                        "inbound": False,
                        "synced_headers": 10,
                        "synced_blocks": 10,
                        "pingtime": 0.02,
                    }
                ],
                "getnetworkinfo": {
                    "networkactive": True,
                    "connections": 1,
                    "localaddresses": [{"address": "198.51.100.20", "port": 8338}],
                    "networks": [
                        {
                            "name": "ipv4",
                            "reachable": True,
                            "limited": False,
                            "proxy": "127.0.0.1:9050",
                        }
                    ],
                },
            }
            return responses[method]

    seen: dict[str, Any] = {}

    class InspectingProvider:
        def diagnose(
            self,
            node: Any,
            *,
            system_prompt: str,
            investigation_prompt: str,
        ) -> Diagnosis:
            seen["peers"] = node.get_peer_information()
            seen["network"] = node.get_network_status()
            return sample_diagnosis()

    diagnose(CoreRpcNodeAdapter(RawTransport()), InspectingProvider())

    assert seen["peers"] == [
        {"inbound": False, "synced_headers": 10, "synced_blocks": 10, "pingtime": 0.02}
    ]
    assert seen["network"] == {
        "networkactive": True,
        "connections": 1,
        "networks": [{"name": "ipv4", "limited": False, "reachable": True}],
    }


def test_provider_failure_propagates_without_workflow_fallback() -> None:
    class ProviderFailure(RuntimeError):
        pass

    failure = ProviderFailure("provider unavailable")

    class FailingProvider:
        def diagnose(self, node: Any, **kwargs: Any) -> Diagnosis:
            raise failure

    with pytest.raises(ProviderFailure) as caught:
        diagnose(FakeNode(), FailingProvider())

    assert caught.value is failure
