from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from corewarden.agent import INVESTIGATION_PROMPT, SYSTEM_PROMPT, build_agent, diagnose
from corewarden.models import Classification, Diagnosis, Evidence


@dataclass
class Result:
    structured_output: Any


class FakeAgent:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, prompt: str, **kwargs: Any) -> Result:
        self.calls.append((prompt, kwargs))
        return Result(self.output)


class FakeNode:
    def get_blockchain_status(self) -> dict[str, Any]:
        return {"blocks": 10, "headers": 10}

    def get_network_status(self) -> dict[str, Any]:
        return {"networkactive": True, "connections": 2}

    def get_peer_information(self) -> list[dict[str, Any]]:
        return [{"id": 1, "synced_blocks": 10}]

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


def test_diagnose_uses_current_structured_output_invocation() -> None:
    expected = sample_diagnosis()
    agent = FakeAgent(expected)

    actual = diagnose(agent)

    assert actual is expected
    assert agent.calls == [
        (INVESTIGATION_PROMPT, {"structured_output_model": Diagnosis})
    ]


def test_diagnose_rejects_missing_structured_output() -> None:
    with pytest.raises(RuntimeError, match="no validated"):
        diagnose(FakeAgent(None))


def test_build_agent_receives_only_four_diagnostic_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class CapturingAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("corewarden.agent.Agent", CapturingAgent)
    agent = build_agent(FakeNode(), "example.model")

    assert isinstance(agent, CapturingAgent)
    assert captured["model"] == "example.model"
    assert captured["system_prompt"] == SYSTEM_PROMPT
    assert captured["callback_handler"] is None
    assert [tool.tool_name for tool in captured["tools"]] == [
        "get_blockchain_status",
        "get_network_status",
        "get_peer_information",
        "get_chain_tips",
    ]
    assert "all four" in SYSTEM_PROMPT
    assert "block age alone" in SYSTEM_PROMPT
    assert "Blocks below headers" in SYSTEM_PROMPT
    assert "Missing or sharply degraded peers" in SYSTEM_PROMPT
    assert "network-wide" in SYSTEM_PROMPT
