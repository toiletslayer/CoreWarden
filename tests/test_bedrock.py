from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest
from botocore.exceptions import ClientError

from corewarden.bedrock import StrandsBedrockProvider
from corewarden.errors import ProviderError
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

    with pytest.raises(ProviderError, match="Bedrock returned no validated"):
        StrandsBedrockProvider("example.model").diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )


def test_bedrock_provider_normalizes_provider_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class ProviderFailure(Exception):
        pass

    class FailingAgent:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __call__(self, prompt: str, **kwargs: Any) -> Result:
            raise ProviderFailure(
                "access denied AWS_ACCESS_KEY_ID=example-do-not-log "
                "session-token=example-do-not-log peer=192.0.2.10"
            )

    monkeypatch.setattr("corewarden.bedrock.Agent", FailingAgent)
    monkeypatch.setenv("AWS_REGION", "us-west-2")

    with (
        caplog.at_level(logging.DEBUG, logger="corewarden.bedrock"),
        pytest.raises(ProviderError) as caught,
    ):
        StrandsBedrockProvider("global.anthropic.claude-sonnet-4-6").diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )

    assert str(caught.value) == (
        "Bedrock provider invocation failed; check AWS credentials, model access, "
        "region, and diagnostic logs."
    )
    diagnostic = caplog.text
    assert "phase=agent_invocation" in diagnostic
    assert "service=bedrock-runtime" in diagnostic
    assert "region=us-west-2" in diagnostic
    assert "model_id=global.anthropic.claude-sonnet-4-6" in diagnostic
    assert "exception=ProviderFailure" in diagnostic
    assert "Provider initialization or invocation failed." in diagnostic
    assert "example-do-not-log" not in diagnostic
    assert "192.0.2.10" not in diagnostic


def test_bedrock_constructor_failure_logs_safe_dependency_metadata_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class MissingDependencyException(Exception):
        pass

    class FailingAgent:
        def __init__(self, **kwargs: Any) -> None:
            raise MissingDependencyException(
                "install botocore[crt]; secretAccessKey=do-not-log; node=192.0.2.20"
            )

    class PoisonNode:
        def get_blockchain_status(self) -> dict[str, Any]:
            raise AssertionError("pre-tool node observation was accessed")

        get_network_status = get_blockchain_status
        get_peer_information = get_blockchain_status
        get_chain_tips = get_blockchain_status

    monkeypatch.setattr("corewarden.bedrock.Agent", FailingAgent)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.delenv("AWS_REGION", raising=False)

    with (
        caplog.at_level(logging.DEBUG, logger="corewarden.bedrock"),
        pytest.raises(ProviderError, match="Bedrock provider invocation failed"),
    ):
        StrandsBedrockProvider("global.anthropic.claude-sonnet-4-6").diagnose(
            PoisonNode(), system_prompt="system", investigation_prompt="investigate"
        )

    diagnostic = caplog.text
    assert "phase=agent_construction" in diagnostic
    assert "exception=MissingDependencyException" in diagnostic
    assert "Required AWS SDK dependency is unavailable." in diagnostic
    assert "region=us-west-2" in diagnostic
    assert "model_id=global.anthropic.claude-sonnet-4-6" in diagnostic
    assert "do-not-log" not in diagnostic
    assert "192.0.2.20" not in diagnostic
    assert "pre-tool node observation" not in diagnostic


def test_bedrock_client_error_logs_code_and_operation_without_aws_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingAgent:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __call__(self, prompt: str, **kwargs: Any) -> Result:
            raise ClientError(
                {
                    "Error": {
                        "Code": "AccessDeniedException",
                        "Message": "AWS_SESSION_TOKEN=example-session-token-do-not-log",
                    }
                },
                "ConverseStream",
            )

    monkeypatch.setattr("corewarden.bedrock.Agent", FailingAgent)

    with (
        caplog.at_level(logging.DEBUG, logger="corewarden.bedrock"),
        pytest.raises(ProviderError, match="Bedrock provider invocation failed"),
    ):
        StrandsBedrockProvider("example.model").diagnose(
            FakeNode(), system_prompt="system", investigation_prompt="investigate"
        )

    diagnostic = caplog.text
    assert "aws_error_code=AccessDeniedException" in diagnostic
    assert "operation=ConverseStream" in diagnostic
    assert "example-session-token-do-not-log" not in diagnostic
