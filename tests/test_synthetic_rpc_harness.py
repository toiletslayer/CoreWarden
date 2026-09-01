from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from corewarden.errors import RpcResponseError, RpcTransportError
from corewarden.monitoring import HealthState, MonitoringService, evaluate_health
from corewarden.rpc import CoreRpcNodeAdapter, JsonRpcHttpTransport
from scripts.synthetic_rpc_harness import (
    ALLOWED_METHODS,
    LOOPBACK_ADDRESS,
    SCENARIO_NAMES,
    TEST_RPC_PASSWORD,
    TEST_RPC_USERNAME,
    ScenarioController,
    SyntheticRpcHarness,
    run_acceptance,
    scenario_payload,
)


def node_for(harness: SyntheticRpcHarness) -> CoreRpcNodeAdapter:
    return CoreRpcNodeAdapter(
        JsonRpcHttpTransport(
            harness.url,
            username=TEST_RPC_USERNAME,
            password=TEST_RPC_PASSWORD,
            timeout_seconds=1,
        )
    )


def test_synthetic_server_binds_only_to_loopback_and_supports_exact_allow_list() -> None:
    assert "host" not in inspect.signature(SyntheticRpcHarness).parameters
    with SyntheticRpcHarness(port=0) as harness:
        assert harness.url.startswith(f"http://{LOOPBACK_ADDRESS}:")
        node = node_for(harness)
        node.get_blockchain_status()
        node.get_network_status()
        node.get_peer_information()
        node.get_chain_tips()

        arbitrary = JsonRpcHttpTransport(
            harness.url,
            username=TEST_RPC_USERNAME,
            password=TEST_RPC_PASSWORD,
            timeout_seconds=1,
        )
        with pytest.raises(RpcResponseError, match="Method not found"):
            arbitrary.call("sendtoaddress")

        assert {method for _, method in harness.calls} == ALLOWED_METHODS
    assert {
        "getblockchaininfo",
        "getnetworkinfo",
        "getpeerinfo",
        "getchaintips",
    } == ALLOWED_METHODS


def test_synthetic_server_requires_isolated_test_authentication() -> None:
    with SyntheticRpcHarness(port=0) as harness:
        unauthenticated = JsonRpcHttpTransport(harness.url, timeout_seconds=1)
        with pytest.raises(RpcTransportError, match="HTTP 401"):
            unauthenticated.call("getblockchaininfo")

    assert TEST_RPC_USERNAME == "corewarden-test"
    assert TEST_RPC_PASSWORD.startswith("test-only-")


@pytest.mark.parametrize(
    ("scenario", "expected_state", "expected_reason"),
    [
        ("healthy", HealthState.HEALTHY, None),
        ("recovered", HealthState.HEALTHY, None),
        (
            "degraded_peer_connectivity",
            HealthState.DEGRADED,
            "No peer connections",
        ),
        ("degraded_header_gap", HealthState.DEGRADED, "Local blocks trail known headers"),
        ("degraded_warning", HealthState.DEGRADED, "Node reports warnings"),
    ],
)
def test_scenarios_drive_real_transport_adapter_and_health_policy(
    scenario: str, expected_state: HealthState, expected_reason: str | None
) -> None:
    controller = ScenarioController(scenario)
    with SyntheticRpcHarness(controller=controller, port=0) as harness:
        result = evaluate_health(node_for(harness))

    assert result.state is expected_state
    if expected_reason is None:
        assert result.reasons == ()
    else:
        assert expected_reason in result.reasons


def test_scenario_control_file_switches_a_running_server(tmp_path: Path) -> None:
    control_file = tmp_path / "scenario.txt"
    control_file.write_text("healthy", encoding="utf-8")
    controller = ScenarioController(control_file=control_file)

    with SyntheticRpcHarness(controller=controller, port=0) as harness:
        node = node_for(harness)
        assert evaluate_health(node).state is HealthState.HEALTHY
        control_file.write_text("degraded_warning", encoding="utf-8")
        assert evaluate_health(node).state is HealthState.DEGRADED


def test_cost_free_acceptance_sequence_proves_escalation_dedup_recovery_and_unavailable() -> None:
    result = run_acceptance()

    assert result["states"] == [
        "healthy",
        "degraded",
        "degraded",
        "degraded",
        "healthy",
        "unavailable",
    ]
    assert result["provider_invocations"] == 2
    assert result["privacy_clean"] is True
    events = result["events"]
    assert sum(message.startswith("AI investigation:") for message in events) == 2
    assert sum(message == "Node recovered" for message in events) == 1
    assert sum(message.startswith("Unavailable:") for message in events) == 1


def test_unchanged_unavailable_endpoint_does_not_invoke_provider() -> None:
    calls = 0
    node = CoreRpcNodeAdapter(JsonRpcHttpTransport("http://127.0.0.1:1", timeout_seconds=0.05))

    def provider() -> None:
        nonlocal calls
        calls += 1

    monitor = MonitoringService(lambda: evaluate_health(node), provider)
    monitor._active = True
    monitor.run_cycle()
    monitor.run_cycle()

    assert monitor.status.current_state is HealthState.UNAVAILABLE
    assert calls == 0
    unavailable_events = [
        event for event in monitor.status.events if event.state is HealthState.UNAVAILABLE
    ]
    assert len(unavailable_events) == 1


def test_fixtures_use_only_documentation_addresses_and_no_secret_material() -> None:
    serialized = json.dumps({name: scenario_payload(name) for name in SCENARIO_NAMES})

    assert "192.0.2." in serialized
    for forbidden in (
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "BEGIN PRIVATE KEY",
        "127.0.0.1:8337",
    ):
        assert forbidden not in serialized
