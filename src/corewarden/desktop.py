"""Testable application logic for the CoreWarden Windows desktop UI."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corewarden.agent import diagnose
from corewarden.bedrock import StrandsBedrockProvider
from corewarden.config import Settings
from corewarden.credentials import (
    OPENAI_CREDENTIAL_TARGET,
    OPENAI_CREDENTIAL_USERNAME,
    CredentialStore,
)
from corewarden.errors import ConfigurationError, CoreWardenError, ProviderError
from corewarden.models import Diagnosis
from corewarden.monitoring import MonitoringService, MonitoringStatus, evaluate_health
from corewarden.openai_provider import OpenAIResponsesProvider
from corewarden.rpc import CoreRpcNodeAdapter, JsonRpcHttpTransport

DEFAULT_DESKTOP_RPC_URL = "http://127.0.0.1:8337"


@dataclass(frozen=True, slots=True)
class DesktopConfiguration:
    provider: str
    rpc_url: str
    rpc_user: str = field(default="", repr=False)
    rpc_password: str = field(default="", repr=False)
    rpc_cookie_path: str = field(default="", repr=False)
    aws_profile: str = ""
    aws_region: str = "us-west-2"
    bedrock_model_id: str = "global.anthropic.claude-sonnet-4-6"

    def settings(self) -> Settings:
        username, password = _resolve_rpc_credentials(self)
        values = {
            "COREWARDEN_RPC_URL": self.rpc_url.strip(),
            "COREWARDEN_RPC_TIMEOUT_SECONDS": "10",
            "COREWARDEN_MODEL_ID": self.bedrock_model_id.strip(),
        }
        if username or password:
            values["COREWARDEN_RPC_USER"] = username
            values["COREWARDEN_RPC_PASSWORD"] = password
        return Settings.from_env(values)


@dataclass(frozen=True, slots=True)
class ProviderTestResult:
    provider: str
    message: str


@dataclass(frozen=True, slots=True)
class NodeTestResult:
    message: str


@dataclass(frozen=True, slots=True)
class DesktopRunResult:
    provider: str
    diagnosis: Diagnosis


def _resolve_rpc_credentials(configuration: DesktopConfiguration) -> tuple[str, str]:
    cookie_path = configuration.rpc_cookie_path.strip()
    explicit_user = configuration.rpc_user
    explicit_password = configuration.rpc_password
    if cookie_path and (explicit_user or explicit_password):
        raise ConfigurationError(
            "Use either an RPC cookie file or an RPC username/password pair, not both."
        )
    if not cookie_path:
        return explicit_user, explicit_password
    try:
        data = Path(cookie_path).read_bytes()
    except OSError:
        raise ConfigurationError("The configured RPC cookie file could not be read.") from None
    if len(data) > 4096:
        raise ConfigurationError("The configured RPC cookie file is invalid.")
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ConfigurationError("The configured RPC cookie file is invalid.") from None
    username, separator, password = text.partition(":")
    if not separator or not username or not password:
        raise ConfigurationError("The configured RPC cookie file is invalid.")
    return username, password


@contextmanager
def _temporary_aws_environment(
    profile: str, region: str, environment: dict[str, str] | None = None
) -> Iterator[None]:
    target = os.environ if environment is None else environment
    changes = {"AWS_PROFILE": profile.strip(), "AWS_REGION": region.strip()}
    previous = {name: target.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value:
                target[name] = value
            else:
                target.pop(name, None)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                target.pop(name, None)
            else:
                target[name] = value


def _test_bedrock_authentication(profile: str, region: str) -> None:
    try:
        import boto3

        session = boto3.Session(
            profile_name=profile.strip() or None,
            region_name=region.strip() or None,
        )
        credentials = session.get_credentials()
        if credentials is None:
            raise RuntimeError("missing credentials")
        credentials.get_frozen_credentials()
        session.client("sts", region_name=region.strip() or None).get_caller_identity()
        session.client("bedrock-runtime", region_name=region.strip() or None)
    except Exception:
        raise ProviderError(
            "Bedrock configuration test failed; authenticate the AWS profile and check the region."
        ) from None


@dataclass(slots=True)
class DesktopService:
    """Coordinate credentials, provider checks, and the existing diagnosis workflow."""

    credential_store: CredentialStore
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ)
    bedrock_tester: Callable[[str, str], None] = field(
        default=_test_bedrock_authentication, repr=False
    )
    node_factory: Callable[[Settings], Any] | None = field(default=None, repr=False)
    diagnosis_runner: Callable[[Any, Any], Diagnosis] = field(default=diagnose, repr=False)
    _diagnosis_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.node_factory is None:
            self.node_factory = self._default_node_factory

    @staticmethod
    def _default_node_factory(settings: Settings) -> CoreRpcNodeAdapter:
        return CoreRpcNodeAdapter(
            JsonRpcHttpTransport(
                url=settings.rpc_url,
                username=settings.rpc_user,
                password=settings.rpc_password,
                timeout_seconds=settings.rpc_timeout_seconds,
            )
        )

    def credential_status(self) -> str:
        try:
            if self.credential_store.get_secret(
                OPENAI_CREDENTIAL_TARGET, OPENAI_CREDENTIAL_USERNAME
            ):
                return "saved"
        except CoreWardenError:
            if self.environment.get("OPENAI_API_KEY"):
                return "environment"
            return "unavailable"
        return "environment" if self.environment.get("OPENAI_API_KEY") else "missing"

    def save_openai_key(self, api_key: str) -> None:
        key = api_key.strip()
        if not key:
            raise ConfigurationError("Paste an OpenAI API key before saving.")
        self.credential_store.set_secret(OPENAI_CREDENTIAL_TARGET, OPENAI_CREDENTIAL_USERNAME, key)

    def remove_openai_key(self) -> None:
        self.credential_store.delete_secret(OPENAI_CREDENTIAL_TARGET, OPENAI_CREDENTIAL_USERNAME)

    def _openai_key(self) -> str:
        try:
            saved = self.credential_store.get_secret(
                OPENAI_CREDENTIAL_TARGET, OPENAI_CREDENTIAL_USERNAME
            )
        except CoreWardenError:
            saved = None
        key = saved or self.environment.get("OPENAI_API_KEY")
        if not key:
            raise ConfigurationError("Save an OpenAI API key in the GUI or set OPENAI_API_KEY.")
        return key

    def test_provider(self, configuration: DesktopConfiguration) -> ProviderTestResult:
        if configuration.provider == "openai":
            OpenAIResponsesProvider(api_key=self._openai_key()).test_configuration()
            return ProviderTestResult(
                provider="openai",
                message="OpenAI authentication and gpt-5.6-luna access succeeded.",
            )
        if configuration.provider != "bedrock":
            raise ConfigurationError("Choose OpenAI or Bedrock.")
        self.bedrock_tester(configuration.aws_profile, configuration.aws_region)
        return ProviderTestResult(
            provider="bedrock",
            message=(
                "AWS authentication succeeded. Model access is confirmed when diagnosis runs."
            ),
        )

    def test_node(self, configuration: DesktopConfiguration) -> NodeTestResult:
        settings = configuration.settings()
        node = self._node(settings)
        node.get_blockchain_status()
        return NodeTestResult(message="Node RPC responded through CoreWarden's read-only adapter.")

    def run_diagnosis(self, configuration: DesktopConfiguration) -> DesktopRunResult:
        settings = configuration.settings()
        node = self._node(settings)
        with self._diagnosis_lock:
            if configuration.provider == "openai":
                provider = OpenAIResponsesProvider(api_key=self._openai_key())
                diagnosis = self.diagnosis_runner(node, provider)
            elif configuration.provider == "bedrock":
                with _temporary_aws_environment(
                    configuration.aws_profile, configuration.aws_region
                ):
                    provider = StrandsBedrockProvider(settings.model_id)
                    diagnosis = self.diagnosis_runner(node, provider)
            else:
                raise ConfigurationError("Choose OpenAI or Bedrock.")
        return DesktopRunResult(provider=configuration.provider, diagnosis=diagnosis)

    def create_monitor(
        self,
        configuration: DesktopConfiguration,
        *,
        interval_seconds: float,
        status_callback: Callable[[MonitoringStatus], None] | None = None,
    ) -> MonitoringService:
        """Build monitoring over the same sanitized adapter and diagnosis workflow."""
        settings = configuration.settings()
        return MonitoringService(
            snapshot_source=lambda: evaluate_health(self._node(settings)),
            diagnosis_runner=lambda: self.run_diagnosis(configuration).diagnosis,
            interval_seconds=interval_seconds,
            status_callback=status_callback,
        )

    def _node(self, settings: Settings) -> Any:
        if self.node_factory is None:
            raise RuntimeError("Desktop node factory was not initialized")
        return self.node_factory(settings)
