from __future__ import annotations

import json
import logging
from typing import Any

from corewarden.cli import main
from corewarden.config import Settings
from corewarden.errors import ConfigurationError
from corewarden.models import Classification, Diagnosis, Evidence


class LiveLikeNode:
    def get_blockchain_status(self) -> dict[str, Any]:
        return {"blocks": 100, "headers": 100, "initialblockdownload": False}

    def get_network_status(self) -> dict[str, Any]:
        return {"networkactive": True, "connections": 2}

    def get_peer_information(self) -> list[dict[str, Any]]:
        return [{"id": 1, "synced_blocks": 100}]

    def get_chain_tips(self) -> list[dict[str, Any]]:
        return [{"height": 100, "branchlen": 0, "status": "active"}]


def report_with_secret() -> Diagnosis:
    return Diagnosis(
        classification=Classification.HEALTHY,
        confidence=0.9,
        summary="observer reports a healthy node",
        evidence=[
            Evidence(
                source="blockchain_status",
                observation="blocks equal headers",
                significance="the node is synchronized to known headers",
            ),
            Evidence(
                source="peer_information",
                observation="peers exist",
                significance="external connectivity is present",
            ),
        ],
        safety_boundary="No remediation was performed.",
    )


def test_main_writes_redacted_diagnostic_evidence(
    monkeypatch: Any, tmp_path: Any, capsys: Any
) -> None:
    evidence_path = tmp_path / "live.json"
    settings = Settings(
        rpc_url="http://127.0.0.1:8337",
        rpc_user="observer",
        rpc_password="very-secret",
        diagnostic_mode=True,
        evidence_path=evidence_path,
    )
    node = LiveLikeNode()

    monkeypatch.setattr("corewarden.cli.Settings.from_env", lambda: settings)
    monkeypatch.setattr("corewarden.cli.CoreRpcNodeAdapter", lambda transport: node)
    provider = object()
    selected_models: list[str] = []

    def fake_provider(model: str) -> object:
        selected_models.append(model)
        return provider

    monkeypatch.setattr("corewarden.cli.StrandsBedrockProvider", fake_provider)

    def fake_diagnose(wrapped_node: Any, received_provider: Any) -> Diagnosis:
        assert received_provider is provider
        wrapped_node.get_blockchain_status()
        wrapped_node.get_network_status()
        wrapped_node.get_peer_information()
        wrapped_node.get_chain_tips()
        return report_with_secret()

    monkeypatch.setattr("corewarden.cli.diagnose", fake_diagnose)

    try:
        assert main() == 0
    finally:
        logging.getLogger("corewarden").handlers.clear()

    captured = capsys.readouterr()
    output = captured.out + captured.err + evidence_path.read_text(encoding="utf-8")
    assert json.loads(captured.out)["classification"] == "healthy"
    assert selected_models == [settings.model_id]
    assert evidence_path.exists()
    assert "observer" not in output
    assert "very-secret" not in output


def test_main_reports_configuration_error_as_json(monkeypatch: Any, capsys: Any) -> None:
    def fail() -> Settings:
        raise ConfigurationError("missing endpoint")

    monkeypatch.setattr("corewarden.cli.Settings.from_env", fail)

    assert main() == 2
    error = json.loads(capsys.readouterr().err)
    assert error == {"error": "ConfigurationError", "message": "missing endpoint"}


def test_main_reports_provider_failure_without_leaking_details(
    monkeypatch: Any, capsys: Any
) -> None:
    class ProviderFailure(Exception):
        pass

    settings = Settings(
        rpc_url="http://127.0.0.1:8337",
        rpc_user="observer",
        rpc_password="very-secret",
    )
    monkeypatch.setattr("corewarden.cli.Settings.from_env", lambda: settings)
    monkeypatch.setattr("corewarden.cli.CoreRpcNodeAdapter", lambda transport: LiveLikeNode())
    monkeypatch.setattr("corewarden.cli.StrandsBedrockProvider", lambda model: object())

    def fail(node: Any, provider: Any) -> Diagnosis:
        raise ProviderFailure("very-secret provider detail")

    monkeypatch.setattr("corewarden.cli.diagnose", fail)

    assert main() == 2
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert error == {
        "error": "ProviderFailure",
        "message": "Agent or model-provider invocation failed; check AWS credentials, "
        "model access, region, and diagnostic logs.",
    }
    assert "very-secret" not in captured.out + captured.err
