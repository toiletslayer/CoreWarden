from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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
    history_export_filename,
    history_row,
    provider_id_from_label,
    provider_visibility,
)
from corewarden.history import LocalPreferences, SanitizedHistoryEvent
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


def test_history_row_and_export_filename_are_human_readable() -> None:
    event = SanitizedHistoryEvent(
        timestamp="2026-09-01T12:34:56Z",
        event_type="investigation_completed",
        state="degraded",
        reason="Investigation completed",
        investigation_occurred=True,
        provider="Amazon Bedrock / Strands",
        classification="suspicious",
        confidence=0.82,
    )

    assert history_row(event, timezone(timedelta(hours=-4), "EDT")) == (
        "2026-09-01 08:34:56-04:00",
        "Investigation Completed",
        "Degraded",
        "Investigation completed",
        "Yes",
        "Amazon Bedrock / Strands",
        "suspicious (82%)",
    )
    assert history_export_filename("json", datetime(2026, 9, 1, 12, 34, 56)) == (
        "corewarden-history-20260901-123456.json"
    )


def test_history_local_formatting_preserves_chronological_order_across_offsets() -> None:
    events = [
        SanitizedHistoryEvent(
            timestamp=f"2026-09-01T12:{minute:02d}:00Z",
            event_type="health",
            state="healthy",
            reason="Healthy",
        )
        for minute in (10, 20)
    ]

    winter_rows = [history_row(event, timezone(timedelta(hours=-5), "EST")) for event in events]
    summer_rows = [history_row(event, timezone(timedelta(hours=-4), "EDT")) for event in events]

    assert [row[0] for row in winter_rows] == [
        "2026-09-01 07:10:00-05:00",
        "2026-09-01 07:20:00-05:00",
    ]
    assert [row[0] for row in summer_rows] == [
        "2026-09-01 08:10:00-04:00",
        "2026-09-01 08:20:00-04:00",
    ]


class FakeStatus:
    def __init__(self, active: bool) -> None:
        self.active = active


class FakeMonitor:
    def __init__(self, active: bool) -> None:
        self.status = FakeStatus(active)
        self.stop_calls: list[bool] = []

    def stop(self, *, wait: bool = True) -> None:
        self.stop_calls.append(wait)
        self.status.active = False


class FakeRoot:
    def __init__(self) -> None:
        self.withdrawals = 0
        self.deiconifications = 0
        self.lifts = 0
        self.focuses = 0
        self.destroys = 0

    def withdraw(self) -> None:
        self.withdrawals += 1

    def deiconify(self) -> None:
        self.deiconifications += 1

    def lift(self) -> None:
        self.lifts += 1

    def focus_force(self) -> None:
        self.focuses += 1

    def destroy(self) -> None:
        self.destroys += 1


class FakeTray:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.refreshes = 0

    def start(self) -> bool:
        self.starts += 1
        return self.starts == 1

    def stop(self) -> bool:
        self.stops += 1
        return True

    def refresh(self) -> None:
        self.refreshes += 1


def desktop_shell(*, active: bool) -> CoreWardenDesktop:
    desktop = object.__new__(CoreWardenDesktop)
    desktop.root = FakeRoot()
    desktop._monitor = FakeMonitor(active)
    desktop._tray = FakeTray()
    desktop._quitting = False
    return desktop


def test_close_hides_only_while_monitoring_and_idle_close_exits() -> None:
    active = desktop_shell(active=True)
    actions: list[str] = []
    active._hide_to_tray = lambda: actions.append("hide")
    active._quit = lambda: actions.append("quit")

    active._close()
    assert actions == ["hide"]

    idle = desktop_shell(active=False)
    idle._hide_to_tray = lambda: actions.append("unexpected-hide")
    idle._quit = lambda: actions.append("idle-quit")
    idle._close()
    assert actions == ["hide", "idle-quit"]


def test_hide_restore_notice_is_bounded_and_does_not_change_monitoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = desktop_shell(active=True)
    preferences = LocalPreferences(tmp_path / "preferences.json")
    desktop.service = type("Service", (), {"preferences": preferences})()
    notices: list[str] = []
    monkeypatch.setattr(
        "corewarden.gui.messagebox.showinfo",
        lambda _title, message, **_kwargs: notices.append(message),
    )

    desktop._hide_to_tray()
    desktop._restore_from_tray()
    desktop._hide_to_tray()

    assert desktop._monitor.status.active is True
    assert desktop._tray.starts == 2
    assert desktop.root.withdrawals == 2
    assert desktop.root.deiconifications == 1
    assert desktop.root.lifts == 1
    assert desktop.root.focuses == 1
    assert len(notices) == 1
    assert "still monitoring in the system tray" in notices[0]


def test_quit_stops_existing_monitor_and_tray_before_destroying_root() -> None:
    desktop = desktop_shell(active=True)

    desktop._quit()
    desktop._quit()

    assert desktop._monitor.stop_calls == [True]
    assert desktop._tray.stops == 1
    assert desktop.root.destroys == 1
