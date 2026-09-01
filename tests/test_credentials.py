from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from corewarden.credentials import WindowsCredentialStore
from corewarden.errors import CredentialStorageError


@dataclass
class FakeCredentialApi:
    credentials: dict[str, tuple[str, str]] = field(default_factory=dict)

    def get(self, target: str) -> tuple[str, str] | None:
        return self.credentials.get(target)

    def set(self, target: str, username: str, secret: str) -> None:
        self.credentials[target] = (username, secret)

    def delete(self, target: str) -> None:
        self.credentials.pop(target, None)


def test_windows_credential_store_round_trip_hides_backend_and_secret() -> None:
    backend = FakeCredentialApi()
    store = WindowsCredentialStore(backend)
    secret = "sk-secret-never-in-repr"

    store.set_secret("CoreWarden/OpenAI", "OPENAI_API_KEY", secret)

    assert store.get_secret("CoreWarden/OpenAI", "OPENAI_API_KEY") == secret
    assert secret not in repr(store)
    assert "FakeCredentialApi" not in repr(store)

    store.delete_secret("CoreWarden/OpenAI", "OPENAI_API_KEY")
    assert store.get_secret("CoreWarden/OpenAI", "OPENAI_API_KEY") is None


def test_windows_credential_store_rejects_empty_or_wrong_credential() -> None:
    backend = FakeCredentialApi({"CoreWarden/OpenAI": ("unexpected", "secret")})
    store = WindowsCredentialStore(backend)

    with pytest.raises(CredentialStorageError, match="required"):
        store.set_secret("CoreWarden/OpenAI", "OPENAI_API_KEY", "")
    with pytest.raises(CredentialStorageError, match="unexpected") as caught:
        store.get_secret("CoreWarden/OpenAI", "OPENAI_API_KEY")

    assert "secret" not in str(caught.value)


def test_windows_credential_store_lazily_builds_api(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeCredentialApi({"target": ("user", "secret")})
    monkeypatch.setattr("corewarden.credentials._WindowsCredentialApi", lambda: backend)
    store = WindowsCredentialStore()

    assert store.get_secret("target", "user") == "secret"
    assert store._credential_api() is backend
