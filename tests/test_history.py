from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from corewarden.history import (
    HISTORY_RETENTION_LIMIT,
    HISTORY_SCHEMA_VERSION,
    HistoryStore,
    LocalPreferences,
    SanitizedHistoryEvent,
    default_history_path,
    local_timestamp_values,
    persisted_event_from_monitoring,
)
from corewarden.monitoring import HealthState, MonitoringEvent


def event(index: int, *, event_type: str = "health") -> SanitizedHistoryEvent:
    return SanitizedHistoryEvent(
        timestamp=f"2026-09-01T00:{index % 60:02d}:00Z",
        event_type=event_type,
        state="healthy",
        reason="Healthy",
        fingerprint_category="healthy",
    )


def test_windows_history_path_uses_non_roaming_local_appdata() -> None:
    path = default_history_path({"LOCALAPPDATA": r"C:\SafeLocalAppData"})

    assert path == Path(r"C:\SafeLocalAppData") / "CoreWarden" / "history" / (
        "monitoring-history.json"
    )


def test_absent_history_is_empty_and_events_survive_restart_in_order(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    store = HistoryStore(path)

    assert store.events() == ()
    assert store.append(event(1)) is True
    assert store.append(event(2)) is True

    restarted = HistoryStore(path)
    assert [item.timestamp for item in restarted.events()] == [
        "2026-09-01T00:01:00Z",
        "2026-09-01T00:02:00Z",
    ]
    assert not path.with_suffix(".json.tmp").exists()


def test_fixed_retention_prunes_oldest_first(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    store = HistoryStore(path)

    for index in range(HISTORY_RETENTION_LIMIT + 5):
        assert store.append(event(index)) is True

    saved = store.events()
    assert len(saved) == HISTORY_RETENTION_LIMIT
    assert saved[0] == event(5)
    assert saved[-1] == event(HISTORY_RETENTION_LIMIT + 4)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert len(document["events"]) == HISTORY_RETENTION_LIMIT


def test_corrupt_history_does_not_block_startup_and_is_preserved_before_save(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.json"
    path.write_text("{not-json", encoding="utf-8")

    store = HistoryStore(path)
    assert store.events() == ()
    assert store.warning == "Saved history could not be read; the existing file was left unchanged."
    assert path.read_text(encoding="utf-8") == "{not-json"

    assert store.append(event(1)) is True
    preserved = list(tmp_path.glob("history.json.corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "{not-json"
    assert json.loads(path.read_text(encoding="utf-8"))["events"][0]["reason"] == "Healthy"


def test_unknown_fields_and_forbidden_values_are_not_loaded_or_persisted(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    forbidden = {
        "peer": "203.0.113.42",
        "hostname": "private-node.example",
        "subver": "/SensitiveClient:1.0/",
        "session_id": "peer-session-99",
        "mapped_as": "AS64500",
        "proxy": "127.0.0.1:9050",
        "rpc_password": "rpc-password-secret",
        "api_key": "sk-fake-secret-value",
        "aws_token": "fake-aws-session-token",
        "account_id": "123456789012",
        "authorization": "Bearer fake-authorization-value",
    }
    valid = event(1).to_dict() | {"unexpected": forbidden}
    unsafe = event(2).to_dict() | {"reason": "Peer 203.0.113.42 failed"}
    path.write_text(json.dumps({"schema_version": 1, "events": [valid, unsafe]}), encoding="utf-8")

    store = HistoryStore(path)
    assert len(store.events()) == 1
    assert store.append(event(3)) is True
    serialized = path.read_text(encoding="utf-8")
    for value in forbidden.values():
        assert value not in serialized
    assert "unexpected" not in serialized


def test_directly_constructed_unsafe_event_is_rejected_before_write(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    store = HistoryStore(path)
    unsafe = SanitizedHistoryEvent(
        timestamp="2026-09-01T00:00:00Z",
        event_type="degradation",
        state="degraded",
        reason="Peer 203.0.113.42 failed with rpc-password-secret",
    )

    assert store.append(unsafe) is False
    assert store.events() == ()
    assert store.warning == "An invalid history event was not saved."
    assert not path.exists()


def test_monitoring_projection_uses_controlled_reason_not_arbitrary_message() -> None:
    source = MonitoringEvent(
        occurred_at=datetime.now(timezone.utc),
        message="203.0.113.42 rpc-password-secret private-node.example",
        state=HealthState.DEGRADED,
        event_type="degradation",
        reasons=("203.0.113.42", "No peer connections"),
        fingerprint_category="not-a-safe-hash",
    )

    persisted = persisted_event_from_monitoring(source)
    assert persisted is not None
    assert persisted.reason == "No peer connections"
    assert persisted.fingerprint_category == "degraded"
    assert "203.0.113.42" not in json.dumps(persisted.to_dict())
    assert "rpc-password-secret" not in json.dumps(persisted.to_dict())


def test_json_and_csv_exports_are_parseable_and_use_only_schema_fields(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.append(event(1))
    json_path = tmp_path / "export.json"
    csv_path = tmp_path / "export.csv"

    store.export_json(json_path)
    store.export_csv(csv_path, timezone(timedelta(hours=-4), "EDT"))

    document = json.loads(json_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == HISTORY_SCHEMA_VERSION
    assert document["event_count"] == 1
    assert document["retention_limit"] == HISTORY_RETENTION_LIMIT
    assert document["events"] == [event(1).to_dict()]
    assert document["events"][0]["timestamp"] == "2026-09-01T00:01:00Z"
    assert "timestamp_local" not in document["events"][0]
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert set(rows[0]) == set(SanitizedHistoryEvent.__dataclass_fields__) | {
        "timestamp_local",
        "timezone",
    }
    assert rows[0]["timestamp"] == "2026-09-01T00:01:00Z"
    assert rows[0]["timestamp_local"] == "2026-08-31T20:01:00-04:00"
    assert rows[0]["timezone"] == "UTC-04:00"
    assert rows[0]["reason"] == "Healthy"


def test_local_timestamp_conversion_is_offset_aware_without_changing_utc_value() -> None:
    canonical = "2026-09-02T12:31:06Z"

    summer, summer_zone = local_timestamp_values(canonical, timezone(timedelta(hours=-4), "EDT"))
    winter, winter_zone = local_timestamp_values(canonical, timezone(timedelta(hours=-5), "EST"))

    assert summer == "2026-09-02T08:31:06-04:00"
    assert summer_zone == "UTC-04:00"
    assert winter == "2026-09-02T07:31:06-05:00"
    assert winter_zone == "UTC-05:00"
    assert canonical == "2026-09-02T12:31:06Z"


def test_local_preferences_notice_is_bounded_and_corruption_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "preferences.json"
    preferences = LocalPreferences(path)
    assert preferences.tray_notice_shown() is False

    preferences.mark_tray_notice_shown()
    assert preferences.tray_notice_shown() is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"tray_notice_shown": True}

    path.write_text("not-json", encoding="utf-8")
    assert preferences.tray_notice_shown() is False
