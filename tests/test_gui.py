from __future__ import annotations

from corewarden.desktop import DesktopRunResult
from corewarden.gui import (
    FIRST_RUN_GUIDANCE,
    PRIVACY_NOTICE,
    PROVIDER_LABELS,
    RESULT_PLACEHOLDER,
    CoreWardenDesktop,
    credential_status_text,
    default_rpc_url,
    format_diagnosis,
    format_monitoring_state,
    format_monitoring_time,
    format_status,
    provider_id_from_label,
    provider_visibility,
)
from corewarden.monitoring import HealthState, MonitoringStatus
from tests.test_agent import sample_diagnosis


class FakeVariable:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class FakeFrame:
    def __init__(self) -> None:
        self.visible = False

    def pack_forget(self) -> None:
        self.visible = False

    def pack(self, **kwargs: object) -> None:
        self.visible = True


def test_format_diagnosis_shows_only_readable_structured_fields() -> None:
    text = format_diagnosis(DesktopRunResult(provider="openai", diagnosis=sample_diagnosis()))

    assert "Provider: OpenAI" in text
    assert "Classification: healthy" in text
    assert "Confidence: 90%" in text
    assert "Evidence:" in text
    assert "no remediation" in text


def test_first_run_and_privacy_copy_describes_the_existing_safe_workflow() -> None:
    for step in ("Choose a provider", "Test Provider", "Test Node", "Run Diagnosis"):
        assert step in FIRST_RUN_GUIDANCE
    for method in (
        "getblockchaininfo",
        "getnetworkinfo",
        "getpeerinfo",
        "getchaintips",
    ):
        assert method in PRIVACY_NOTICE
    assert "filtered locally before model access" in PRIVACY_NOTICE
    assert "Windows Credential Manager" in PRIVACY_NOTICE
    assert "does not intentionally persist raw RPC credentials" in PRIVACY_NOTICE


def test_rpc_default_uses_canonical_target_and_allows_environment_override() -> None:
    assert default_rpc_url({}) == "http://127.0.0.1:8337"
    assert default_rpc_url({"COREWARDEN_RPC_URL": "http://127.0.0.1:18443"}) == (
        "http://127.0.0.1:18443"
    )


def test_provider_labels_and_visibility_keep_internal_ids_private() -> None:
    assert PROVIDER_LABELS == {"openai": "OpenAI", "bedrock": "Amazon Bedrock"}
    assert provider_id_from_label("OpenAI") == "openai"
    assert provider_id_from_label("Amazon Bedrock") == "bedrock"
    assert provider_visibility("OpenAI").openai is True
    assert provider_visibility("OpenAI").bedrock is False
    assert provider_visibility("Amazon Bedrock").openai is False
    assert provider_visibility("Amazon Bedrock").bedrock is True


def test_provider_switch_hides_irrelevant_settings_without_clearing_values() -> None:
    desktop = object.__new__(CoreWardenDesktop)
    desktop.provider = FakeVariable("OpenAI")
    desktop.openai_frame = FakeFrame()
    desktop.bedrock_frame = FakeFrame()
    desktop.openai_key = FakeVariable("entered-openai-value")
    desktop.aws_profile = FakeVariable("entered-aws-profile")
    desktop.bedrock_model = FakeVariable("entered-bedrock-model")
    desktop._provider_test_state = "Ready"
    desktop._node_test_state = "Connected"
    desktop.status = FakeVariable("")

    desktop._sync_provider_settings()
    assert desktop.openai_frame.visible is True
    assert desktop.bedrock_frame.visible is False

    desktop.provider.set("Amazon Bedrock")
    desktop._sync_provider_settings(object())
    assert desktop.openai_frame.visible is False
    assert desktop.bedrock_frame.visible is True
    assert desktop.openai_key.get() == "entered-openai-value"
    assert desktop.aws_profile.get() == "entered-aws-profile"
    assert desktop.bedrock_model.get() == "entered-bedrock-model"
    assert desktop.status.get() == "Provider: Not tested | Node: Connected"


def test_status_placeholder_and_credential_wording_are_human_readable() -> None:
    assert format_status("Not tested", "Not tested") == ("Provider: Not tested | Node: Not tested")
    assert RESULT_PLACEHOLDER == "Diagnosis results will appear here."
    assert credential_status_text("saved") == "OpenAI credential: Saved securely"
    assert credential_status_text("environment") == (
        "OpenAI credential: Available from environment"
    )
    assert credential_status_text("missing") == "OpenAI credential: Not configured"
    assert "secret" not in credential_status_text("unavailable").lower()


def test_monitoring_status_formatting_is_concise() -> None:
    off = MonitoringStatus(False, None, None, None, "Never", ())
    active = MonitoringStatus(True, HealthState.DEGRADED, None, None, "Failed", ())

    assert format_monitoring_state(off) == "Off"
    assert format_monitoring_state(active) == "Degraded"
    assert format_monitoring_time(None) == "Never"
