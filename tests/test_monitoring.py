from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from corewarden.errors import RpcTransportError
from corewarden.history import HistoryStore, persisted_event_from_monitoring
from corewarden.monitoring import (
    DEFAULT_AUTOMATIC_BUDGET_WINDOW_SECONDS,
    DEFAULT_AUTOMATIC_CALL_LIMIT,
    DEFAULT_AUTOMATIC_COOLDOWN_SECONDS,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_INCIDENT_LIMIT,
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


class GapNode(HealthyNode):
    def __init__(self, gap: int) -> None:
        self.gap = gap

    def get_blockchain_status(self) -> dict[str, Any]:
        return {
            "blocks": 1_000,
            "headers": 1_000 + self.gap,
            "verificationprogress": 1.0,
            "initialblockdownload": False,
            "warnings": "",
        }

    def get_peer_information(self) -> list[dict[str, Any]]:
        return [{"synced_blocks": 1_000}]

    def get_chain_tips(self) -> list[dict[str, Any]]:
        return [{"height": 1_000, "branchlen": 0, "status": "active"}]


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
        automatic_cooldown_seconds=0,
        automatic_call_limit=100,
    )
    service._active = True
    return service


def test_default_interval_is_five_minutes_and_sub_minute_is_rejected() -> None:
    service = MonitoringService(lambda: snapshot(HealthState.HEALTHY, "h"), sample_diagnosis)

    assert DEFAULT_MONITORING_INTERVAL_SECONDS == 300
    assert DEFAULT_RECURRENCE_COOLDOWN_SECONDS == 1800
    assert DEFAULT_AUTOMATIC_COOLDOWN_SECONDS == 3600
    assert DEFAULT_AUTOMATIC_CALL_LIMIT == 6
    assert DEFAULT_AUTOMATIC_BUDGET_WINDOW_SECONDS == 86400
    assert DEFAULT_INCIDENT_LIMIT == 128
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


def test_changing_height_gap_within_one_severity_bucket_does_not_storm_provider() -> None:
    snapshots = [evaluate_health(GapNode(gap)) for gap in (500, 400, 250, 51)]
    diagnoses: list[str] = []
    service = service_for(snapshots, diagnoses)

    for _ in snapshots:
        service.run_cycle()

    assert {item.normalized["height_gap_category"] for item in snapshots} == {
        "large_51_to_500"
    }
    assert len({item.fingerprint for item in snapshots}) == 1
    assert diagnoses == ["called"]


def test_crossing_height_gap_severity_bucket_is_a_material_condition_change() -> None:
    very_large = evaluate_health(GapNode(501))
    large = evaluate_health(GapNode(500))
    diagnoses: list[str] = []
    service = service_for([very_large, large], diagnoses)

    service.run_cycle()
    service.run_cycle()

    assert very_large.normalized["height_gap_category"] == "very_large_over_500"
    assert large.normalized["height_gap_category"] == "large_51_to_500"
    assert very_large.fingerprint != large.fingerprint
    assert diagnoses == ["called", "called"]


def test_global_cooldown_blocks_different_fingerprint_storm_then_retries_pending() -> None:
    clock = MonotonicClock()
    diagnoses: list[str] = []
    values = iter(
        [
            snapshot(HealthState.DEGRADED, "fault-a", "No peer connections"),
            snapshot(HealthState.DEGRADED, "fault-b", "Node reports warnings"),
            snapshot(HealthState.DEGRADED, "fault-b", "Node reports warnings"),
            snapshot(HealthState.DEGRADED, "fault-b", "Node reports warnings"),
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
    assert diagnoses == ["called"]
    assert list(service._pending_fingerprints) == ["fault-b"]
    assert service.status.automatic_cooldown_remaining_seconds == 3600

    clock.advance(3599)
    service.run_cycle()
    assert diagnoses == ["called"]
    assert service.status.automatic_cooldown_remaining_seconds == 1

    clock.advance(1)
    service.run_cycle()
    assert diagnoses == ["called", "called"]
    assert not service._pending_fingerprints


def test_rolling_automatic_call_budget_caps_calls_and_reports_remaining_state() -> None:
    clock = MonotonicClock()
    diagnoses: list[str] = []
    values = iter(
        [
            snapshot(HealthState.DEGRADED, "fault-a", "condition a"),
            snapshot(HealthState.DEGRADED, "fault-b", "condition b"),
            snapshot(HealthState.DEGRADED, "fault-c", "condition c"),
            snapshot(HealthState.DEGRADED, "fault-c", "condition c"),
        ]
    )
    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=lambda: diagnoses.append("called") or sample_diagnosis(),
        monotonic_clock=clock,
        automatic_cooldown_seconds=0,
        automatic_call_limit=2,
    )
    service._active = True

    service.run_cycle()
    service.run_cycle()
    service.run_cycle()

    assert diagnoses == ["called", "called"]
    assert list(service._pending_fingerprints) == ["fault-c"]
    assert service.status.automatic_calls_remaining == 0
    assert service.status.automatic_call_limit == 2
    assert service.status.automatic_budget_window_seconds == 86400

    clock.advance(DEFAULT_AUTOMATIC_BUDGET_WINDOW_SECONDS)
    service.run_cycle()

    assert diagnoses == ["called", "called", "called"]
    assert not service._pending_fingerprints
    assert service.status.automatic_calls_remaining == 1


def test_known_recurring_fault_deferred_by_recurrence_runs_when_unchanged_and_eligible() -> None:
    clock = MonotonicClock()
    diagnoses: list[str] = []
    values = iter(
        [
            snapshot(HealthState.DEGRADED, "fault-a", "condition a"),
            snapshot(HealthState.HEALTHY, "healthy"),
            snapshot(HealthState.DEGRADED, "fault-a", "condition a"),
            snapshot(HealthState.DEGRADED, "fault-a", "condition a"),
        ]
    )
    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=lambda: diagnoses.append("called") or sample_diagnosis(),
        monotonic_clock=clock,
        automatic_cooldown_seconds=0,
    )
    service._active = True

    service.run_cycle()
    service.run_cycle()
    clock.advance(DEFAULT_RECURRENCE_COOLDOWN_SECONDS - 1)
    service.run_cycle()

    assert diagnoses == ["called"]
    assert list(service._pending_fingerprints) == ["fault-a"]

    clock.advance(1)
    service.run_cycle()

    assert diagnoses == ["called", "called"]
    assert not service._pending_fingerprints


def test_pending_condition_is_cleared_when_condition_disappears() -> None:
    clock = MonotonicClock()
    values = iter(
        [
            snapshot(HealthState.DEGRADED, "fault-a", "condition a"),
            snapshot(HealthState.DEGRADED, "fault-b", "condition b"),
            snapshot(HealthState.HEALTHY, "healthy"),
        ]
    )
    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=sample_diagnosis,
        monotonic_clock=clock,
    )
    service._active = True

    service.run_cycle()
    service.run_cycle()
    assert list(service._pending_fingerprints) == ["fault-b"]

    service.run_cycle()

    assert not service._pending_fingerprints


def test_incident_storage_is_bounded_to_most_recent_conditions() -> None:
    fingerprints = [f"fault-{index}" for index in range(6)]
    values = iter(
        [snapshot(HealthState.DEGRADED, item, "changed condition") for item in fingerprints]
    )
    service = MonitoringService(
        snapshot_source=lambda: next(values),
        diagnosis_runner=sample_diagnosis,
        automatic_cooldown_seconds=0,
        automatic_call_limit=10,
        incident_limit=3,
    )
    service._active = True

    for _ in fingerprints:
        service.run_cycle()

    assert list(service._incident_boundaries) == fingerprints[-3:]


def test_pending_incident_storage_is_bounded() -> None:
    service = MonitoringService(
        snapshot_source=lambda: snapshot(HealthState.HEALTHY, "healthy"),
        diagnosis_runner=sample_diagnosis,
        incident_limit=3,
    )

    for index in range(6):
        service._remember_pending(f"pending-{index}")

    assert list(service._pending_fingerprints) == ["pending-3", "pending-4", "pending-5"]


@pytest.mark.parametrize(
    "configuration",
    [
        {"automatic_cooldown_seconds": -1},
        {"recurrence_cooldown_seconds": float("nan")},
        {"automatic_call_limit": 0},
        {"automatic_budget_window_seconds": 59},
        {"incident_limit": True},
    ],
)
def test_monitoring_guardrail_configuration_is_validated(
    configuration: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        MonitoringService(
            lambda: snapshot(HealthState.HEALTHY, "healthy"),
            sample_diagnosis,
            **configuration,
        )


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
        automatic_cooldown_seconds=0,
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
        automatic_cooldown_seconds=0,
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


def test_rapid_restart_is_refused_until_prior_monitor_thread_exits() -> None:
    first_entered = threading.Event()
    release_first = threading.Event()
    second_checked = threading.Event()
    calls = 0
    running = 0
    maximum_running = 0
    counter_lock = threading.Lock()

    def controlled_snapshot() -> HealthSnapshot:
        nonlocal calls, running, maximum_running
        with counter_lock:
            calls += 1
            current_call = calls
            running += 1
            maximum_running = max(maximum_running, running)
        try:
            if current_call == 1:
                first_entered.set()
                release_first.wait(1)
            else:
                second_checked.set()
            return snapshot(HealthState.HEALTHY, "healthy")
        finally:
            with counter_lock:
                running -= 1

    service = MonitoringService(controlled_snapshot, sample_diagnosis, interval_seconds=60)
    assert service.start() is True
    assert first_entered.wait(1)
    assert service.stop(wait=False) is True

    assert service.start() is False

    release_first.set()
    assert service._thread is not None
    service._thread.join(1)
    assert not service._thread.is_alive()

    assert service.start() is True
    assert second_checked.wait(1)
    assert service.stop() is True
    assert calls == 2
    assert maximum_running == 1


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
