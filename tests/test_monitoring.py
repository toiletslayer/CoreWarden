from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from corewarden.errors import RpcTransportError
from corewarden.history import HistoryStore, persisted_event_from_monitoring
from corewarden.monitoring import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_MONITORING_INTERVAL_SECONDS,
    DEFAULT_RECURRENCE_COOLDOWN_SECONDS,
    HealthSnapshot,
    HealthState,
    MonitoringService,
    evaluate_health,
)
from tests.test_agent import sample_diagnosis


class HealthyNode:
    def get_blockchain_status(self) -> dict[str, Any]:
        return {
            "blocks": 100,
            "headers": 100,
            "verificationprogress": 1.0,
            "initialblockdownload": False,
            "warnings": "",
        }

    def get_network_status(self) -> dict[str, Any]:
        return {"networkactive": True, "connections": 2, "warnings": ""}

    def get_peer_information(self) -> list[dict[str, Any]]:
        return [{"synced_blocks": 100}, {"synced_blocks": 100}]

    def get_chain_tips(self) -> list[dict[str, Any]]:
        return [{"height": 100, "branchlen": 0, "status": "active"}]


class MonotonicClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def snapshot(state: HealthState, fingerprint: str, *reasons: str) -> HealthSnapshot:
    return HealthSnapshot(
        state=state,
        reasons=tuple(reasons),
        fingerprint=fingerprint,
        checked_at=datetime.now(timezone.utc),
        normalized={},
    )


def service_for(snapshots: list[HealthSnapshot], diagnoses: list[str]) -> MonitoringService:
    values = iter(snapshots)
    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=lambda: diagnoses.append("called") or sample_diagnosis(),
    )
    service._active = True
    return service


def test_default_interval_is_five_minutes_and_sub_minute_is_rejected() -> None:
    service = MonitoringService(lambda: snapshot(HealthState.HEALTHY, "h"), sample_diagnosis)

    assert DEFAULT_MONITORING_INTERVAL_SECONDS == 300
    assert DEFAULT_RECURRENCE_COOLDOWN_SECONDS == 1800
    assert service.interval_seconds == 300
    try:
        MonitoringService(lambda: snapshot(HealthState.HEALTHY, "h"), sample_diagnosis, 59)
    except ValueError as exc:
        assert "at least 60" in str(exc)
    else:
        raise AssertionError("sub-minute monitoring was accepted")


def test_deterministic_healthy_snapshot_uses_all_four_observations() -> None:
    node = HealthyNode()
    result = evaluate_health(node)

    assert result.state is HealthState.HEALTHY
    assert result.reasons == ()
    assert result.normalized["blocks"] == 100
    assert result.normalized["peer_count"] == 2
    assert len(result.fingerprint) == 64


def test_healthy_to_healthy_does_not_invoke_ai_or_fill_history() -> None:
    diagnoses: list[str] = []
    service = service_for(
        [snapshot(HealthState.HEALTHY, "same"), snapshot(HealthState.HEALTHY, "same")],
        diagnoses,
    )

    assert service.run_cycle() is True
    first_count = len(service.status.events)
    assert service.run_cycle() is True

    assert diagnoses == []
    assert len(service.status.events) == first_count == 1


def test_healthy_to_degraded_invokes_once_and_same_condition_is_deduplicated() -> None:
    diagnoses: list[str] = []
    service = service_for(
        [
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "no-peers", "No peer connections"),
            snapshot(HealthState.DEGRADED, "no-peers", "No peer connections"),
        ],
        diagnoses,
    )

    service.run_cycle()
    service.run_cycle()
    service.run_cycle()

    assert diagnoses == ["called"]
    assert sum("AI investigation:" in event.message for event in service.status.events) == 1


def test_materially_changed_degradation_can_trigger_new_investigation() -> None:
    diagnoses: list[str] = []
    service = service_for(
        [
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "no-peers", "No peer connections"),
            snapshot(
                HealthState.DEGRADED,
                "network-off",
                "No peer connections",
                "Node networking is inactive",
            ),
        ],
        diagnoses,
    )

    service.run_cycle()
    service.run_cycle()
    service.run_cycle()

    assert diagnoses == ["called", "called"]


def test_same_fault_recurrence_is_suppressed_until_recovery_cooldown_expires() -> None:
    clock = MonotonicClock()
    diagnoses: list[str] = []
    values = iter(
        [
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
        ]
    )
    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=lambda: diagnoses.append("called") or sample_diagnosis(),
        monotonic_clock=clock,
    )
    service._active = True

    service.run_cycle()
    service.run_cycle()
    service.run_cycle()
    clock.advance(DEFAULT_RECURRENCE_COOLDOWN_SECONDS - 1)
    service.run_cycle()
    service.run_cycle()
    assert diagnoses == ["called"]

    clock.advance(DEFAULT_RECURRENCE_COOLDOWN_SECONDS + 1)
    service.run_cycle()
    service.run_cycle()

    assert diagnoses == ["called", "called"]
    assert sum(event.event_type == "recovery" for event in service.status.events) == 2
    assert sum(event.event_type == "investigation_started" for event in service.status.events) == 2


def test_rapid_same_fault_flapping_within_cooldown_does_not_storm_provider() -> None:
    clock = MonotonicClock()
    diagnoses: list[str] = []
    values = iter(
        [
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
        ]
    )
    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=lambda: diagnoses.append("called") or sample_diagnosis(),
        monotonic_clock=clock,
    )
    service._active = True

    for _ in range(6):
        service.run_cycle()
        clock.advance(60)

    assert diagnoses == ["called"]
    assert sum(event.event_type == "recovery" for event in service.status.events) == 2


def test_recovery_is_recorded_without_recovery_ai_call() -> None:
    diagnoses: list[str] = []
    service = service_for(
        [
            snapshot(HealthState.DEGRADED, "problem", "No peer connections"),
            snapshot(HealthState.HEALTHY, "healthy"),
        ],
        diagnoses,
    )

    service.run_cycle()
    service.run_cycle()

    assert diagnoses == ["called"]
    assert any(event.message == "Node recovered" for event in service.status.events)


def test_rpc_unavailable_has_stable_fingerprint_and_never_retries_ai() -> None:
    class UnavailableNode(HealthyNode):
        def get_blockchain_status(self) -> dict[str, Any]:
            raise RpcTransportError("127.0.0.1 secret endpoint unavailable")

    first = evaluate_health(UnavailableNode())
    second = evaluate_health(UnavailableNode())
    diagnoses: list[str] = []
    service = service_for([first, second], diagnoses)

    service.run_cycle()
    service.run_cycle()

    assert first.state is HealthState.UNAVAILABLE
    assert first.fingerprint == second.fingerprint
    assert diagnoses == []
    history = json.dumps([event.message for event in service.status.events])
    assert "127.0.0.1" not in history
    assert "secret" not in history


def test_provider_failure_is_recorded_once_and_monitoring_continues() -> None:
    values = iter(
        [
            snapshot(HealthState.DEGRADED, "problem", "No peer connections"),
            snapshot(HealthState.DEGRADED, "problem", "No peer connections"),
        ]
    )
    calls = 0

    def fail() -> Any:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider secret detail")

    service = MonitoringService(lambda: next(values), fail)
    service._active = True
    service.run_cycle()
    service.run_cycle()

    assert calls == 1
    assert service.status.last_ai_status == "Failed"
    history = " ".join(event.message for event in service.status.events)
    assert "provider secret detail" not in history
    assert "deterministic monitoring continues" in history


def test_failed_investigation_uses_the_same_recurrence_cooldown_policy() -> None:
    clock = MonotonicClock()
    values = iter(
        [
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
        ]
    )
    calls = 0

    def fail() -> Any:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider secret detail")

    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=fail,
        monotonic_clock=clock,
    )
    service._active = True

    for _ in range(5):
        service.run_cycle()
    assert calls == 1

    clock.advance(DEFAULT_RECURRENCE_COOLDOWN_SECONDS)
    service.run_cycle()
    service.run_cycle()

    assert calls == 2
    assert sum(event.event_type == "investigation_failed" for event in service.status.events) == 2


def test_unexpected_snapshot_failure_becomes_safe_unavailable_state() -> None:
    service = MonitoringService(
        lambda: (_ for _ in ()).throw(RuntimeError("rpc-password-do-not-leak")),
        lambda: (_ for _ in ()).throw(AssertionError("AI must not run")),
    )
    service._active = True

    assert service.run_cycle() is True
    assert service.status.current_state is HealthState.UNAVAILABLE
    assert "rpc-password-do-not-leak" not in " ".join(
        event.message for event in service.status.events
    )


def test_start_stop_are_safe_and_duplicate_loops_are_rejected() -> None:
    checked = threading.Event()
    service = MonitoringService(
        lambda: checked.set() or snapshot(HealthState.HEALTHY, "healthy"),
        sample_diagnosis,
        interval_seconds=60,
    )

    assert service.start() is True
    assert checked.wait(1)
    assert service.start() is False
    assert service.stop() is True
    assert service.stop() is False
    assert service.status.active is False


def test_monitoring_cycles_cannot_overlap() -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_snapshot() -> HealthSnapshot:
        entered.set()
        release.wait(1)
        return snapshot(HealthState.HEALTHY, "healthy")

    service = MonitoringService(slow_snapshot, sample_diagnosis, interval_seconds=60)
    assert service.start() is True
    assert entered.wait(1)

    assert service.run_cycle() is False
    release.set()
    service.stop()


def test_event_history_is_bounded_and_contains_only_controlled_messages() -> None:
    snapshots = [
        snapshot(HealthState.DEGRADED, f"condition-{index}", f"condition {index}")
        for index in range(DEFAULT_HISTORY_LIMIT + 5)
    ]
    service = service_for(snapshots, [])

    for _ in snapshots:
        service.run_cycle()

    assert len(service.status.events) == DEFAULT_HISTORY_LIMIT
    history = json.dumps([event.message for event in service.status.events])
    for forbidden in ("addr", "hostname", "subver", "session_id", "mapped_as", "proxy"):
        assert forbidden not in history


def test_partial_observations_degrade_without_arbitrary_node_calls() -> None:
    class PartialNode(HealthyNode):
        calls: list[str] = []

        def get_blockchain_status(self) -> dict[str, Any]:
            self.calls.append("blockchain")
            return super().get_blockchain_status()

        def get_network_status(self) -> dict[str, Any]:
            self.calls.append("network")
            raise RpcTransportError("failed")

        def get_peer_information(self) -> list[dict[str, Any]]:
            self.calls.append("peers")
            return super().get_peer_information()

        def get_chain_tips(self) -> list[dict[str, Any]]:
            self.calls.append("tips")
            return super().get_chain_tips()

    node = PartialNode()
    result = evaluate_health(node)

    assert result.state is HealthState.DEGRADED
    assert node.calls == ["blockchain", "network", "peers", "tips"]
    assert "Incomplete network status" in result.reasons


def test_persistent_audit_trail_records_transitions_and_deduplicated_investigation(
    tmp_path: Path,
) -> None:
    values = iter(
        [
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "no-peers", "No peer connections"),
            snapshot(HealthState.DEGRADED, "no-peers", "No peer connections"),
            snapshot(HealthState.HEALTHY, "recovered"),
        ]
    )
    calls: list[str] = []
    store = HistoryStore(tmp_path / "history.json")

    def persist(source: Any) -> None:
        projected = persisted_event_from_monitoring(source)
        assert projected is not None
        store.append(projected)

    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=lambda: calls.append("provider") or sample_diagnosis(),
        event_callback=persist,
        provider_name="Amazon Bedrock / Strands",
    )
    service._active = True

    for _ in range(4):
        service.run_cycle()

    assert calls == ["provider"]
    events = store.events()
    assert [item.event_type for item in events] == [
        "health",
        "degradation",
        "investigation_started",
        "investigation_completed",
        "recovery",
    ]
    completed = next(item for item in events if item.event_type == "investigation_completed")
    assert completed.provider == "Amazon Bedrock / Strands"
    assert completed.classification == "healthy"
    assert completed.confidence == 0.9


def test_provider_failure_persists_only_safe_failure_category(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")

    def persist(source: Any) -> None:
        projected = persisted_event_from_monitoring(source)
        assert projected is not None
        store.append(projected)

    service = MonitoringService(
        snapshot_source=lambda: snapshot(HealthState.DEGRADED, "problem", "No peer connections"),
        diagnosis_runner=lambda: (_ for _ in ()).throw(
            RuntimeError("sk-fake private-node.example 203.0.113.42")
        ),
        event_callback=persist,
        provider_name="OpenAI",
    )
    service._active = True

    service.run_cycle()

    failed = next(item for item in store.events() if item.event_type == "investigation_failed")
    assert failed.provider == "OpenAI"
    assert failed.provider_failure_category == "provider_invocation_failed"
    serialized = (tmp_path / "history.json").read_text(encoding="utf-8")
    for forbidden in ("sk-fake", "private-node.example", "203.0.113.42"):
        assert forbidden not in serialized
