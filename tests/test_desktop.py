from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from corewarden.credentials import OPENAI_CREDENTIAL_TARGET, OPENAI_CREDENTIAL_USERNAME
from corewarden.desktop import (
    DesktopConfiguration,
    DesktopService,
    _temporary_aws_environment,
)
from corewarden.errors import ConfigurationError, CredentialStorageError
from tests.test_agent import FakeNode, sample_diagnosis


@dataclass
class FakeStore:
    secret: str | None = None
    writes: list[tuple[str, str, str]] = field(default_factory=list)
    unavailable: bool = False

    def get_secret(self, target: str, username: str) -> str | None:
        if self.unavailable:
            raise CredentialStorageError("unavailable")
        return self.secret

    def set_secret(self, target: str, username: str, secret: str) -> None:
        self.writes.append((target, username, secret))
        self.secret = secret

    def delete_secret(self, target: str, username: str) -> None:
        self.secret = None


def configuration(provider: str = "openai", **kwargs: Any) -> DesktopConfiguration:
    values = {"provider": provider, "rpc_url": "http://127.0.0.1:8337"}
    values.update(kwargs)
    return DesktopConfiguration(**values)


def test_desktop_configuration_reads_rpc_cookie_without_persisting_it(tmp_path: Any) -> None:
    cookie = tmp_path / ".cookie"
    cookie.write_text("cookie-user:cookie-secret\n", encoding="utf-8")
    config = configuration(rpc_cookie_path=str(cookie))

    settings = config.settings()

    assert settings.rpc_user == "cookie-user"
    assert settings.rpc_password == "cookie-secret"
    assert "cookie-secret" not in repr(config)


def test_desktop_configuration_rejects_conflicting_or_invalid_rpc_auth(tmp_path: Any) -> None:
    with pytest.raises(ConfigurationError, match="either an RPC cookie"):
        configuration(rpc_user="user", rpc_password="password", rpc_cookie_path="cookie").settings()

    invalid = tmp_path / ".cookie"
    invalid.write_text("not-a-cookie", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid"):
        configuration(rpc_cookie_path=str(invalid)).settings()

    with pytest.raises(ConfigurationError, match="could not be read"):
        configuration(rpc_cookie_path=str(tmp_path / "missing-cookie")).settings()

    oversized = tmp_path / "oversized-cookie"
    oversized.write_bytes(b"x" * 4097)
    with pytest.raises(ConfigurationError, match="invalid"):
        configuration(rpc_cookie_path=str(oversized)).settings()

    undecodable = tmp_path / "undecodable-cookie"
    undecodable.write_bytes(b"\xff\xfe\xff")
    with pytest.raises(ConfigurationError, match="invalid"):
        configuration(rpc_cookie_path=str(undecodable)).settings()


def test_openai_credential_status_save_remove_and_environment_fallback() -> None:
    store = FakeStore()
    service = DesktopService(store, environment={})
    assert service.credential_status() == "missing"

    service.save_openai_key(" test-secret ")
    assert store.writes == [(OPENAI_CREDENTIAL_TARGET, OPENAI_CREDENTIAL_USERNAME, "test-secret")]
    assert service.credential_status() == "saved"
    service.remove_openai_key()
    assert service.credential_status() == "missing"

    fallback = DesktopService(FakeStore(unavailable=True), environment={"OPENAI_API_KEY": "env"})
    assert fallback.credential_status() == "environment"
    unavailable = DesktopService(FakeStore(unavailable=True), environment={})
    assert unavailable.credential_status() == "unavailable"
    with pytest.raises(ConfigurationError, match="Paste an OpenAI"):
        service.save_openai_key("  ")
    with pytest.raises(ConfigurationError, match="Save an OpenAI"):
        service.test_provider(configuration())


def test_provider_configuration_checks_are_explicit_and_do_not_build_node(
    monkeypatch: Any,
) -> None:
    received_keys: list[str] = []

    class FakeOpenAIProvider:
        def __init__(self, *, api_key: str) -> None:
            received_keys.append(api_key)

        def test_configuration(self) -> None:
            pass

    node_calls = 0

    def node_factory(settings: Any) -> FakeNode:
        nonlocal node_calls
        node_calls += 1
        return FakeNode()

    monkeypatch.setattr("corewarden.desktop.OpenAIResponsesProvider", FakeOpenAIProvider)
    service = DesktopService(FakeStore("saved-key"), environment={}, node_factory=node_factory)

    result = service.test_provider(configuration())

    assert result.provider == "openai"
    assert received_keys == ["saved-key"]
    assert node_calls == 0


def test_bedrock_configuration_check_uses_profile_and_region() -> None:
    checks: list[tuple[str, str]] = []
    service = DesktopService(
        FakeStore(),
        environment={},
        bedrock_tester=lambda profile, region: checks.append((profile, region)),
    )

    result = service.test_provider(
        configuration("bedrock", aws_profile="judge", aws_region="us-east-1")
    )

    assert result.provider == "bedrock"
    assert checks == [("judge", "us-east-1")]


def test_node_test_and_gui_diagnosis_use_existing_workflow(monkeypatch: Any) -> None:
    node = FakeNode()
    diagnoses: list[tuple[Any, Any]] = []

    class FakeOpenAIProvider:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "saved-key"

    monkeypatch.setattr("corewarden.desktop.OpenAIResponsesProvider", FakeOpenAIProvider)

    def run(received_node: Any, received_provider: Any) -> Any:
        diagnoses.append((received_node, received_provider))
        return sample_diagnosis()

    service = DesktopService(
        FakeStore("saved-key"),
        environment={},
        node_factory=lambda settings: node,
        diagnosis_runner=run,
    )
    assert "read-only adapter" in service.test_node(configuration()).message

    result = service.run_diagnosis(configuration())

    assert result.provider == "openai"
    assert result.diagnosis == sample_diagnosis()
    assert diagnoses == [(node, diagnoses[0][1])]
    assert isinstance(diagnoses[0][1], FakeOpenAIProvider)


def test_temporary_aws_environment_restores_values() -> None:
    environment = {"AWS_PROFILE": "original", "OTHER": "value"}

    with _temporary_aws_environment("judge", "us-west-2", environment):
        assert environment["AWS_PROFILE"] == "judge"
        assert environment["AWS_REGION"] == "us-west-2"

    assert environment == {"AWS_PROFILE": "original", "OTHER": "value"}


def test_bedrock_diagnosis_uses_existing_workflow_and_restores_environment(
    monkeypatch: Any,
) -> None:
    node = FakeNode()
    selected: list[tuple[Any, Any]] = []

    class FakeBedrockProvider:
        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

    monkeypatch.setattr("corewarden.desktop.StrandsBedrockProvider", FakeBedrockProvider)
    monkeypatch.setenv("AWS_PROFILE", "original")
    monkeypatch.delenv("AWS_REGION", raising=False)

    def run(received_node: Any, received_provider: Any) -> Any:
        selected.append((received_node, received_provider))
        assert os.environ["AWS_PROFILE"] == "judge"
        assert os.environ["AWS_REGION"] == "us-east-2"
        return sample_diagnosis()

    service = DesktopService(
        FakeStore(), environment={}, node_factory=lambda settings: node, diagnosis_runner=run
    )
    result = service.run_diagnosis(
        configuration(
            "bedrock",
            aws_profile="judge",
            aws_region="us-east-2",
            bedrock_model_id="bedrock.model",
        )
    )

    assert result.provider == "bedrock"
    assert selected[0][0] is node
    assert selected[0][1].model_id == "bedrock.model"
    assert os.environ["AWS_PROFILE"] == "original"
    assert "AWS_REGION" not in os.environ


def test_desktop_service_rejects_unknown_provider() -> None:
    service = DesktopService(FakeStore(), environment={}, node_factory=lambda settings: FakeNode())

    with pytest.raises(ConfigurationError, match="Choose OpenAI or Bedrock"):
        service.test_provider(configuration("other"))
    with pytest.raises(ConfigurationError, match="Choose OpenAI or Bedrock"):
        service.run_diagnosis(configuration("other"))


def test_desktop_monitor_reuses_sanitized_node_and_selected_provider(monkeypatch: Any) -> None:
    node = FakeNode()
    diagnoses: list[Any] = []

    class FakeOpenAIProvider:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "saved-key"

    monkeypatch.setattr("corewarden.desktop.OpenAIResponsesProvider", FakeOpenAIProvider)

    def run(received_node: Any, provider: Any) -> Any:
        diagnoses.append((received_node, provider))
        return sample_diagnosis()

    service = DesktopService(
        FakeStore("saved-key"),
        environment={},
        node_factory=lambda settings: node,
        diagnosis_runner=run,
    )
    monitor = service.create_monitor(configuration(), interval_seconds=300)
    monitor._active = True

    assert monitor.run_cycle() is True
    assert diagnoses == []
    assert monitor.status.current_state.value == "healthy"
