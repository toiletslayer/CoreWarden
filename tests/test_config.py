import pytest

from corewarden.config import Settings
from corewarden.errors import ConfigurationError


def test_loads_minimal_environment() -> None:
    settings = Settings.from_env({"COREWARDEN_RPC_URL": "http://127.0.0.1:18443"})

    assert settings.rpc_url == "http://127.0.0.1:18443"
    assert settings.rpc_user is None
    assert settings.rpc_timeout_seconds == 10
    assert settings.model_id == "global.anthropic.claude-sonnet-4-6"
    assert settings.diagnostic_mode is False
    assert settings.evidence_path.name == "corewarden-evidence.json"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "COREWARDEN_RPC_URL is required"),
        ({"COREWARDEN_RPC_URL": "localhost:8332"}, "must be an http:// or https:// URL"),
        (
            {"COREWARDEN_RPC_URL": "http://user:secret@localhost:8332"},
            "Do not embed RPC credentials",
        ),
        (
            {"COREWARDEN_RPC_URL": "http://localhost", "COREWARDEN_RPC_USER": "only-user"},
            "must be set together",
        ),
        (
            {"COREWARDEN_RPC_URL": "http://localhost", "COREWARDEN_RPC_TIMEOUT_SECONDS": "0"},
            "greater than 0",
        ),
        (
            {"COREWARDEN_RPC_URL": "http://localhost", "COREWARDEN_DIAGNOSTIC_MODE": "maybe"},
            "must be true/false",
        ),
        (
            {"COREWARDEN_RPC_URL": "http://localhost", "COREWARDEN_EVIDENCE_PATH": " "},
            "cannot be empty",
        ),
    ],
)
def test_rejects_unsafe_or_invalid_configuration(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env(env)


def test_loads_credentials_and_overrides() -> None:
    settings = Settings.from_env(
        {
            "COREWARDEN_RPC_URL": "https://node.example/rpc",
            "COREWARDEN_RPC_USER": "observer",
            "COREWARDEN_RPC_PASSWORD": "secret",
            "COREWARDEN_RPC_TIMEOUT_SECONDS": "2.5",
            "COREWARDEN_MODEL_ID": "example.model-v1",
            "COREWARDEN_DIAGNOSTIC_MODE": "yes",
            "COREWARDEN_EVIDENCE_PATH": "evidence/test.json",
        }
    )

    assert settings.rpc_user == "observer"
    assert settings.rpc_password == "secret"
    assert settings.rpc_timeout_seconds == 2.5
    assert settings.model_id == "example.model-v1"
    assert settings.diagnostic_mode is True
    assert settings.evidence_path.as_posix() == "evidence/test.json"
