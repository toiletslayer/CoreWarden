from __future__ import annotations

import csv
import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from corewarden.agent import diagnose
from corewarden.errors import ProviderError
from corewarden.history import HistoryStore, persisted_event_from_monitoring
from corewarden.monitoring import HealthState, MonitoringEvent, MonitoringService, evaluate_health
from corewarden.rpc import CoreRpcNodeAdapter, JsonRpcHttpTransport
from corewarden.tools import create_diagnostic_tools
from scripts.synthetic_rpc_harness import (
    ALLOWED_METHODS,
    TEST_RPC_PASSWORD,
    TEST_RPC_USERNAME,
    FakeDiagnosisProvider,
    ScenarioController,
    SyntheticRpcHarness,
    scenario_payload,
)
from tests.test_agent import sample_diagnosis


def node_for(
    harness: SyntheticRpcHarness,
    *,
    username: str = TEST_RPC_USERNAME,
    password: str = TEST_RPC_PASSWORD,
    timeout: float = 0.5,
) -> CoreRpcNodeAdapter:
    return CoreRpcNodeAdapter(
        JsonRpcHttpTransport(
            harness.url,
            username=username,
            password=password,
            timeout_seconds=timeout,
        )
    )


def activate(service: MonitoringService) -> MonitoringService:
    service._active = True
    return service


def persisted_callback(store: HistoryStore) -> Callable[[MonitoringEvent], None]:
    def persist(event: MonitoringEvent) -> None:
        projected = persisted_event_from_monitoring(event)
        assert projected is not None
        assert store.append(projected) is True

    return persist


@pytest.mark.parametrize("failure", ["connection_refused", "wrong_credentials"])
def test_rpc_unavailable_and_auth_failure_recover_without_provider(
    failure: str, tmp_path: Path
) -> None:
    with SyntheticRpcHarness(port=0) as harness:
        healthy_node = node_for(harness)
        if failure == "wrong_credentials":
            failing_node = node_for(harness, password="test-only-wrong-password")
        else:
            failing_node = CoreRpcNodeAdapter(
                JsonRpcHttpTransport("http://127.0.0.1:1", timeout_seconds=0.05)
            )
        snapshots = iter(
            [
                evaluate_health(healthy_node),
                evaluate_health(failing_node),
                evaluate_health(failing_node),
                evaluate_health(healthy_node),
            ]
        )

    provider_calls: list[str] = []
    store = HistoryStore(tmp_path / f"{failure}.json")
    monitor = activate(
        MonitoringService(
            snapshot_source=lambda: next(snapshots),
            diagnosis_runner=lambda: provider_calls.append("unexpected") or sample_diagnosis(),
            event_callback=persisted_callback(store),
        )
    )
    for _ in range(4):
        assert monitor.run_cycle() is True

    assert provider_calls == []
    assert [event.event_type for event in store.events()] == ["health", "unavailable", "recovery"]
    assert store.events()[1].reason == "Node RPC is unavailable"
    serialized = (tmp_path / f"{failure}.json").read_text(encoding="utf-8")
    assert "wrong-password" not in serialized
    assert "HTTP 401" not in serialized


def test_server_side_unauthorized_state_is_deduplicated_and_recovers(tmp_path: Path) -> None:
    controller = ScenarioController("healthy")
    provider_calls: list[str] = []
    store = HistoryStore(tmp_path / "server-unauthorized.json")
    with SyntheticRpcHarness(controller=controller, port=0) as harness:
        node = node_for(harness)
        monitor = activate(
            MonitoringService(
                snapshot_source=lambda: evaluate_health(node),
                diagnosis_runner=lambda: provider_calls.append("unexpected") or sample_diagnosis(),
                event_callback=persisted_callback(store),
            )
        )
        monitor.run_cycle()
        controller.set("rpc_unauthorized")
        monitor.run_cycle()
        monitor.run_cycle()
        controller.set("healthy")
        monitor.run_cycle()

    assert provider_calls == []
    assert [event.event_type for event in store.events()] == ["health", "unavailable", "recovery"]
    serialized = (tmp_path / "server-unauthorized.json").read_text(encoding="utf-8")
    assert "authorization failure" not in serialized
    assert "HTTP 401" not in serialized


def test_meaningful_degradations_flapping_and_recovery_have_bounded_escalation(
    tmp_path: Path,
) -> None:
    controller = ScenarioController("healthy")
    provider_calls: list[str] = []
    store = HistoryStore(tmp_path / "flapping.json")
    with SyntheticRpcHarness(controller=controller, port=0) as harness:
        node = node_for(harness)
        monitor = activate(
            MonitoringService(
                snapshot_source=lambda: evaluate_health(node),
                diagnosis_runner=lambda: (
                    provider_calls.append(controller.current()) or sample_diagnosis()
                ),
                event_callback=persisted_callback(store),
                provider_name="Amazon Bedrock / Strands",
            )
        )
        sequence = [
            "healthy",
            "degraded_peer_connectivity",
            "degraded_peer_connectivity",
            "healthy",
            "degraded_peer_connectivity",
            "healthy",
            "degraded_header_gap",
            "degraded_header_gap",
            "degraded_warning",
            "degraded_warning",
            "healthy",
        ]
        invocation_counts = []
        for scenario in sequence:
            controller.set(scenario)
            assert monitor.run_cycle() is True
            invocation_counts.append(len(provider_calls))

    assert invocation_counts == [0, 1, 1, 1, 1, 1, 2, 2, 3, 3, 3]
    assert provider_calls == [
        "degraded_peer_connectivity",
        "degraded_header_gap",
        "degraded_warning",
    ]
    event_types = [event.event_type for event in store.events()]
    assert event_types.count("investigation_started") == 3
    assert event_types.count("investigation_completed") == 3
    assert event_types.count("recovery") == 3
    assert event_types.count("degradation") == 4


@pytest.mark.parametrize(
    ("scenario", "expected_state", "expected_reason", "expected_calls", "provider_calls"),
    [
        (
            "partial_getblockchaininfo_failure",
            HealthState.UNAVAILABLE,
            "Node RPC is unavailable",
            ["getblockchaininfo"],
            0,
        ),
        (
            "partial_getnetworkinfo_failure",
            HealthState.DEGRADED,
            "Incomplete network status",
            ["getblockchaininfo", "getnetworkinfo", "getpeerinfo", "getchaintips"],
            1,
        ),
        (
            "partial_getpeerinfo_failure",
            HealthState.DEGRADED,
            "Incomplete peer information",
            ["getblockchaininfo", "getnetworkinfo", "getpeerinfo", "getchaintips"],
            1,
        ),
        (
            "partial_getchaintips_failure",
            HealthState.DEGRADED,
            "Incomplete chain tips",
            ["getblockchaininfo", "getnetworkinfo", "getpeerinfo", "getchaintips"],
            1,
        ),
    ],
)
def test_partial_rpc_failures_are_controlled_deduplicated_and_recover(
    scenario: str,
    expected_state: HealthState,
    expected_reason: str,
    expected_calls: list[str],
    provider_calls: int,
    tmp_path: Path,
) -> None:
    controller = ScenarioController("healthy")
    invocations: list[str] = []
    store = HistoryStore(tmp_path / f"{scenario}.json")
    with SyntheticRpcHarness(controller=controller, port=0) as harness:
        node = node_for(harness)
        monitor = activate(
            MonitoringService(
                snapshot_source=lambda: evaluate_health(node),
                diagnosis_runner=lambda: invocations.append("provider") or sample_diagnosis(),
                event_callback=persisted_callback(store),
            )
        )
        monitor.run_cycle()
        controller.set(scenario)
        before = len(harness.calls)
        monitor.run_cycle()
        first_failure_calls = [method for _, method in harness.calls[before:]]
        monitor.run_cycle()
        controller.set("healthy")
        monitor.run_cycle()

    assert first_failure_calls == expected_calls
    assert monitor.status.current_state is HealthState.HEALTHY
    assert len(invocations) == provider_calls
    assert any(expected_reason in event.reason for event in store.events())
    serialized = json.dumps([event.to_dict() for event in store.events()])
    assert "Synthetic read failure" not in serialized
    assert "-32000" not in serialized
    assert any(event.event_type == "recovery" for event in store.events())


@pytest.mark.parametrize(
    ("scenario", "expected_state", "provider_calls"),
    [
        ("malformed_json", HealthState.UNAVAILABLE, 0),
        ("response_not_object", HealthState.UNAVAILABLE, 0),
        ("missing_result", HealthState.UNAVAILABLE, 0),
        ("invalid_network_result_type", HealthState.DEGRADED, 1),
    ],
)
def test_malformed_http_responses_fail_safely_and_recover(
    scenario: str, expected_state: HealthState, provider_calls: int, tmp_path: Path
) -> None:
    controller = ScenarioController("healthy")
    invocations: list[str] = []
    store = HistoryStore(tmp_path / f"{scenario}.json")
    with SyntheticRpcHarness(controller=controller, port=0) as harness:
        node = node_for(harness)
        monitor = activate(
            MonitoringService(
                snapshot_source=lambda: evaluate_health(node),
                diagnosis_runner=lambda: invocations.append("provider") or sample_diagnosis(),
                event_callback=persisted_callback(store),
            )
        )
        monitor.run_cycle()
        controller.set(scenario)
        monitor.run_cycle()
        assert monitor.status.current_state is expected_state
        monitor.run_cycle()
        controller.set("healthy")
        monitor.run_cycle()

    assert len(invocations) == provider_calls
    assert monitor.status.current_state is HealthState.HEALTHY
    assert any(event.event_type == "recovery" for event in store.events())
    serialized = (tmp_path / f"{scenario}.json").read_text(encoding="utf-8")
    for forbidden in ("malformed synthetic response", "not an object", "omitted result"):
        assert forbidden not in serialized


def test_timeout_is_bounded_deduplicated_and_recovers_without_provider(tmp_path: Path) -> None:
    controller = ScenarioController("healthy")
    store = HistoryStore(tmp_path / "timeout.json")
    provider_calls: list[str] = []
    with SyntheticRpcHarness(controller=controller, port=0) as harness:
        node = node_for(harness, timeout=0.05)
        monitor = activate(
            MonitoringService(
                snapshot_source=lambda: evaluate_health(node),
                diagnosis_runner=lambda: provider_calls.append("unexpected") or sample_diagnosis(),
                event_callback=persisted_callback(store),
            )
        )
        monitor.run_cycle()
        controller.set("rpc_timeout")
        started = time.monotonic()
        monitor.run_cycle()
        elapsed = time.monotonic() - started
        monitor.run_cycle()
        controller.set("healthy")
        monitor.run_cycle()

    assert elapsed < 0.5
    assert provider_calls == []
    assert [event.event_type for event in store.events()] == ["health", "unavailable", "recovery"]


@pytest.mark.parametrize(
    "failure_factory",
    [
        pytest.param(lambda: TimeoutError("192.0.2.55 timeout"), id="timeout"),
        pytest.param(lambda: RuntimeError("throttling fake-account 123456789012"), id="throttle"),
        pytest.param(lambda: ProviderError("generic provider failure"), id="provider-error"),
    ],
)
def test_provider_failure_variations_are_once_only_safe_and_recover(
    failure_factory: Callable[[], Exception], tmp_path: Path
) -> None:
    snapshots: Iterator[Any] = iter(
        [
            evaluate_health_from_scenario("degraded_peer_connectivity"),
            evaluate_health_from_scenario("degraded_peer_connectivity"),
            evaluate_health_from_scenario("healthy"),
        ]
    )
    calls = 0
    store = HistoryStore(tmp_path / "provider-failure.json")

    def fail() -> Any:
        nonlocal calls
        calls += 1
        raise failure_factory()

    monitor = activate(
        MonitoringService(
            snapshot_source=lambda: next(snapshots),
            diagnosis_runner=fail,
            event_callback=persisted_callback(store),
            provider_name="Amazon Bedrock / Strands",
        )
    )
    for _ in range(3):
        monitor.run_cycle()

    assert calls == 1
    assert [event.event_type for event in store.events()] == [
        "degradation",
        "investigation_started",
        "investigation_failed",
        "recovery",
    ]
    serialized = (tmp_path / "provider-failure.json").read_text(encoding="utf-8")
    for forbidden in ("192.0.2.55", "123456789012", "throttling"):
        assert forbidden not in serialized


def evaluate_health_from_scenario(name: str) -> Any:
    payload = scenario_payload(name)

    class ScenarioNode:
        def get_blockchain_status(self) -> dict[str, Any]:
            return payload["getblockchaininfo"]

        def get_network_status(self) -> dict[str, Any]:
            return payload["getnetworkinfo"]

        def get_peer_information(self) -> list[dict[str, Any]]:
            return payload["getpeerinfo"]

        def get_chain_tips(self) -> list[dict[str, Any]]:
            return payload["getchaintips"]

    return evaluate_health(ScenarioNode())


def test_adversarial_identifiers_cannot_reach_provider_history_or_exports(
    tmp_path: Path,
) -> None:
    forbidden = [
        "192.0.2.55",
        "peer.example.invalid",
        "/Satoshi:31.0.0/",
        "fake-peer-session-identifier",
        "AS64500",
        "127.0.0.1:8337",
        "proxy.example.invalid:9050",
        "Authorization: Bearer fake-token-value",
        "sk-fake-api-key-shaped-value",
        "123456789012",
        r"C:\Users\PrivateUser\node\secret.conf",
    ]
    payload = scenario_payload("healthy")
    payload["getnetworkinfo"].update(
        {"hostname": forbidden[1], "authorization": forbidden[7], "account": forbidden[9]}
    )
    payload["getpeerinfo"][0].update(
        {
            "addr": forbidden[0],
            "hostname": forbidden[1],
            "subver": forbidden[2],
            "session_id": forbidden[3],
            "mapped_as": forbidden[4],
            "addrbind": forbidden[5],
            "proxy": forbidden[6],
            "api_key": forbidden[8],
            "private_path": forbidden[10],
        }
    )

    class AdversarialTransport:
        calls: list[str] = []

        def call(self, method: str) -> Any:
            self.calls.append(method)
            return payload[method]

    transport = AdversarialTransport()
    node = CoreRpcNodeAdapter(transport)
    provider = FakeDiagnosisProvider()
    diagnose(node, provider)
    provider_payload = json.dumps(provider.observations)
    assert transport.calls == [
        "getblockchaininfo",
        "getnetworkinfo",
        "getpeerinfo",
        "getchaintips",
    ]

    store = HistoryStore(tmp_path / "privacy.json")
    source = MonitoringEvent(
        occurred_at=datetime.now(timezone.utc),
        message=" ".join(forbidden),
        state=HealthState.DEGRADED,
        event_type="degradation",
        reasons=("No peer connections", *forbidden),
        fingerprint_category="unsafe-arbitrary-category",
    )
    projected = persisted_event_from_monitoring(source)
    assert projected is not None
    assert store.append(projected) is True
    json_export = tmp_path / "privacy-export.json"
    csv_export = tmp_path / "privacy-export.csv"
    store.export_json(json_export)
    store.export_csv(csv_export)
    serialized_outputs = "\n".join(
        [
            provider_payload,
            (tmp_path / "privacy.json").read_text(encoding="utf-8"),
            json_export.read_text(encoding="utf-8"),
            csv_export.read_text(encoding="utf-8"),
        ]
    )
    for value in forbidden:
        assert value not in serialized_outputs


def test_rpc_and_strands_tool_surfaces_remain_exact_and_fixed() -> None:
    node = object()
    expected_tools = {
        "get_blockchain_status",
        "get_network_status",
        "get_peer_information",
        "get_chain_tips",
    }

    assert CoreRpcNodeAdapter._ALLOWED_METHODS == ALLOWED_METHODS
    assert {tool.tool_name for tool in create_diagnostic_tools(node)} == expected_tools


def test_strands_tool_failures_replace_raw_node_exception_with_static_safe_text() -> None:
    secret = (
        "192.0.2.55 Authorization: Bearer fake-token-value "
        r"C:\Users\PrivateUser\node\secret.conf"
    )

    class FailingNode:
        def fail(self) -> Any:
            raise RuntimeError(secret)

        get_blockchain_status = fail
        get_network_status = fail
        get_peer_information = fail
        get_chain_tips = fail

    for tool in create_diagnostic_tools(FailingNode()):
        with pytest.raises(RuntimeError) as caught:
            tool._tool_func()
        message = str(caught.value)
        assert message == (
            "The fixed read-only node tool failed; treat its evidence as unavailable."
        )
        assert secret not in message


def test_start_stop_start_and_shutdown_during_slow_cycle_do_not_deadlock() -> None:
    entered = threading.Event()
    release = threading.Event()
    checks = 0

    def slow_snapshot() -> Any:
        nonlocal checks
        checks += 1
        entered.set()
        release.wait(1)
        return evaluate_health_from_scenario("healthy")

    monitor = MonitoringService(slow_snapshot, sample_diagnosis, interval_seconds=60)
    assert monitor.start() is True
    assert entered.wait(1)
    assert monitor.start() is False
    assert monitor.run_cycle() is False
    assert monitor.stop(wait=True, timeout=0.01) is True
    assert monitor.stop() is False
    release.set()
    assert monitor._thread is not None
    monitor._thread.join(1)
    assert monitor.status.active is False

    entered.clear()
    release.set()
    assert monitor.start() is True
    assert entered.wait(1)
    assert monitor.stop() is True
    assert checks == 2


def test_failure_sequence_survives_restart_without_replay_or_external_calls(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.json"
    first = HistoryStore(path)
    events = [
        MonitoringEvent(
            occurred_at=datetime.now(timezone.utc),
            message="Degraded",
            state=HealthState.DEGRADED,
            event_type="degradation",
            reasons=("No peer connections",),
        ),
        MonitoringEvent(
            occurred_at=datetime.now(timezone.utc),
            message="AI investigation failed",
            state=HealthState.DEGRADED,
            event_type="investigation_failed",
            provider="Amazon Bedrock / Strands",
        ),
        MonitoringEvent(
            occurred_at=datetime.now(timezone.utc),
            message="Node recovered",
            state=HealthState.HEALTHY,
            event_type="recovery",
        ),
    ]
    for event in events:
        projected = persisted_event_from_monitoring(event)
        assert projected is not None
        assert first.append(projected) is True

    restarted = HistoryStore(path)
    assert [event.event_type for event in restarted.events()] == [
        "degradation",
        "investigation_failed",
        "recovery",
    ]
    assert len(restarted.events()) == len(first.events())


def test_retention_exports_exactly_the_newest_thousand_events(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "retention.json")
    for index in range(1005):
        event = MonitoringEvent(
            occurred_at=datetime.fromtimestamp(index, tz=timezone.utc),
            message="Healthy",
            state=HealthState.HEALTHY,
            event_type="health",
        )
        projected = persisted_event_from_monitoring(event)
        assert projected is not None
        assert store.append(projected) is True

    json_export = tmp_path / "retention-export.json"
    csv_export = tmp_path / "retention-export.csv"
    store.export_json(json_export)
    store.export_csv(csv_export)
    document = json.loads(json_export.read_text(encoding="utf-8"))
    with csv_export.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(store.events()) == 1000
    assert len(document["events"]) == 1000
    assert len(rows) == 1000
    assert document["events"][0]["timestamp"] == store.events()[0].timestamp
    assert document["events"][-1]["timestamp"] == store.events()[-1].timestamp
