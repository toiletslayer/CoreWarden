from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from corewarden.bedrock import StrandsBedrockProvider
from corewarden.models import Diagnosis
from tests.test_agent import FakeNode, sample_diagnosis


@dataclass
class Result:
    structured_output: Any


def test_bedrock_provider_preserves_strands_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = sample_diagnosis()
    constructed: dict[str, Any] = {}
    invocations: list[tuple[str, dict[str, Any]]] = []

    class CapturingAgent:
        def __init__(self, **kwargs: Any) -> None:
            constructed.update(kwargs)

        def __call__(self, prompt: str, **kwargs: Any) -> Result:
            invocations.append((prompt, kwargs))
            return Result(expected)

    monkeypatch.setattr("corewarden.bedrock.Agent", CapturingAgent)
    provider = StrandsBedrockProvider("example.model")

    actual = provider.diagnose(
        FakeNode(), system_prompt="system policy", investigation_prompt="investigate now"
    )

    assert actual is expected
    assert constructed["model"] == "example.model"
    assert constructed["system_prompt"] == "system policy"
    assert constructed["callback_handler"] is None
    assert [tool.tool_name for tool in constructed["tools"]] == [
        "get_blockchain_status",
        "get_network_status",
        "get_peer_information",
        "get_chain_tips",
    ]
    assert invocations == [("investigate now", {"structured_output_model": Diagnosis})]


def test_bedrock_provider_rejects_missing_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyAgent:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __call__(self, prompt: str, **kwargs: Any) -> Result:
            return Result(None)

    monkeypatch.setattr("corewarden.bedrock.Agent", EmptyAgent)

    with pytest.raises(RuntimeError, match="Strands returned no validated"):
        StrandsBedrockProvider("example.model").diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )


def test_bedrock_provider_does_not_wrap_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProviderFailure(Exception):
        pass

    failure = ProviderFailure("access denied")

    class FailingAgent:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __call__(self, prompt: str, **kwargs: Any) -> Result:
            raise failure

    monkeypatch.setattr("corewarden.bedrock.Agent", FailingAgent)

    with pytest.raises(ProviderFailure) as caught:
        StrandsBedrockProvider("example.model").diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )

    assert caught.value is failure
