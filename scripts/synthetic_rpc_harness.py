"""Loopback-only synthetic Core RPC server and monitoring acceptance demo.

This is development tooling. It is not imported or started by CoreWarden's
production entry points.
"""

from __future__ import annotations

import argparse
import base64
import json
import tempfile
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from corewarden.agent import diagnose
from corewarden.models import Classification, Diagnosis, Evidence
from corewarden.monitoring import MonitoringService, evaluate_health
from corewarden.rpc import CoreRpcNodeAdapter, JsonRpcHttpTransport

LOOPBACK_ADDRESS = "127.0.0.1"
DEFAULT_PORT = 18443
TEST_RPC_USERNAME = "corewarden-test"
TEST_RPC_PASSWORD = "test-only-password"
DEFAULT_SCENARIO_FILE = Path(tempfile.gettempdir()) / "corewarden-synthetic-scenario.txt"
ALLOWED_METHODS = frozenset({"getblockchaininfo", "getnetworkinfo", "getpeerinfo", "getchaintips"})
SCENARIO_NAMES = (
    "healthy",
    "degraded_peer_connectivity",
    "degraded_header_gap",
    "degraded_warning",
    "recovered",
)
_MAX_REQUEST_BYTES = 64 * 1024


def _healthy() -> dict[str, Any]:
    height = 250_000
    return {
        "getblockchaininfo": {
            "chain": "synthetic-main",
            "blocks": height,
            "headers": height,
            "verificationprogress": 1.0,
            "initialblockdownload": False,
            "warnings": "",
        },
        "getnetworkinfo": {
            "networkactive": True,
            "connections": 3,
            "connections_in": 1,
            "connections_out": 2,
            "warnings": "",
            "subversion": "/SyntheticCore:0.0-test/",
            "localaddresses": [{"address": "192.0.2.20", "port": 18444}],
            "networks": [
                {
                    "name": "ipv4",
                    "limited": False,
                    "reachable": True,
                    "proxy": "127.0.0.1:19050",
                }
            ],
        },
        "getpeerinfo": [
            {
                "id": index,
                "addr": f"192.0.2.{index}:18444",
                "addrbind": "127.0.0.1:50000",
                "subver": "/SyntheticPeer:0.0-test/",
                "mapped_as": 64500,
                "inbound": index == 1,
                "connection_type": "inbound" if index == 1 else "outbound-full-relay",
                "synced_headers": height,
                "synced_blocks": height,
                "pingtime": 0.01 * index,
            }
            for index in range(1, 4)
        ],
        "getchaintips": [{"height": height, "branchlen": 0, "status": "active"}],
    }


def scenario_payload(name: str) -> dict[str, Any]:
    """Return an isolated four-method fixture for one supported scenario."""
    if name not in SCENARIO_NAMES:
        raise ValueError(f"Unknown synthetic scenario: {name}")
    payload = _healthy()
    if name == "degraded_peer_connectivity":
        payload["getnetworkinfo"]["connections"] = 0
        payload["getnetworkinfo"]["connections_in"] = 0
        payload["getnetworkinfo"]["connections_out"] = 0
        payload["getpeerinfo"] = []
    elif name == "degraded_header_gap":
        payload["getblockchaininfo"]["blocks"] = 249_975
        payload["getblockchaininfo"]["verificationprogress"] = 0.998
        payload["getchaintips"][0]["height"] = 249_975
        for peer in payload["getpeerinfo"]:
            peer["synced_blocks"] = 249_975
    elif name == "degraded_warning":
        payload["getblockchaininfo"]["warnings"] = "Synthetic acceptance warning"
    return deepcopy(payload)


@dataclass(slots=True)
class ScenarioController:
    """Resolve the current scenario from memory or a tiny local control file."""

    scenario: str = "healthy"
    control_file: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def set(self, scenario: str) -> None:
        if scenario not in SCENARIO_NAMES:
            raise ValueError(f"Unknown synthetic scenario: {scenario}")
        with self._lock:
            self.scenario = scenario

    def current(self) -> str:
        with self._lock:
            selected = self.scenario
            control_file = self.control_file
        if control_file is not None and control_file.is_file():
            candidate = control_file.read_text(encoding="utf-8").strip()
            if candidate not in SCENARIO_NAMES:
                raise ValueError(f"Scenario file must contain one of: {', '.join(SCENARIO_NAMES)}")
            selected = candidate
        return selected


class _SyntheticServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, controller: ScenarioController) -> None:
        self.controller = controller
        self.calls: list[tuple[str, str]] = []
        self.calls_lock = threading.Lock()
        super().__init__((LOOPBACK_ADDRESS, port), _SyntheticHandler)


class _SyntheticHandler(BaseHTTPRequestHandler):
    server: _SyntheticServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, document: dict[str, Any]) -> None:
        body = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = base64.b64encode(f"{TEST_RPC_USERNAME}:{TEST_RPC_PASSWORD}".encode()).decode(
            "ascii"
        )
        return self.headers.get("Authorization") == f"Basic {token}"

    def do_POST(self) -> None:  # noqa: N802
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._send(403, {"error": "loopback clients only"})
            return
        if not self._authorized():
            self._send(401, {"error": "test authentication required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > _MAX_REQUEST_BYTES:
            self._send(400, {"error": "invalid request size"})
            return
        try:
            request = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "invalid JSON"})
            return
        request_id = request.get("id") if isinstance(request, dict) else None
        method = request.get("method") if isinstance(request, dict) else None
        params = request.get("params") if isinstance(request, dict) else None
        if method not in ALLOWED_METHODS:
            self._send(
                200,
                {
                    "result": None,
                    "error": {"code": -32601, "message": "Method not found"},
                    "id": request_id,
                },
            )
            return
        if params != []:
            self._send(
                200,
                {
                    "result": None,
                    "error": {"code": -32602, "message": "Invalid params"},
                    "id": request_id,
                },
            )
            return
        try:
            scenario = self.server.controller.current()
            result = scenario_payload(scenario)[method]
        except (OSError, ValueError):
            self._send(
                200,
                {
                    "result": None,
                    "error": {"code": -32603, "message": "Invalid synthetic scenario"},
                    "id": request_id,
                },
            )
            return
        with self.server.calls_lock:
            self.server.calls.append((scenario, method))
        self._send(200, {"result": result, "error": None, "id": request_id})


@dataclass(slots=True)
class SyntheticRpcHarness:
    """Own a loopback server suitable for tests, demos, and safe shutdown."""

    controller: ScenarioController = field(default_factory=ScenarioController)
    port: int = DEFAULT_PORT
    _server: _SyntheticServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Synthetic RPC harness is not running")
        return f"http://{LOOPBACK_ADDRESS}:{self._server.server_port}"

    @property
    def calls(self) -> tuple[tuple[str, str], ...]:
        if self._server is None:
            return ()
        with self._server.calls_lock:
            return tuple(self._server.calls)

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("Synthetic RPC harness is already running")
        self._server = _SyntheticServer(self.port, self.controller)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="corewarden-synthetic-rpc",
        )
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(2)
        self._server = None
        self._thread = None

    def __enter__(self) -> SyntheticRpcHarness:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()


@dataclass(slots=True)
class FakeDiagnosisProvider:
    """Cost-free provider that observes the same sanitized four-tool surface."""

    invocations: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)

    def diagnose(
        self,
        node: Any,
        *,
        system_prompt: str,
        investigation_prompt: str,
    ) -> Diagnosis:
        del system_prompt, investigation_prompt
        self.invocations += 1
        self.observations.append(
            {
                "blockchain": dict(node.get_blockchain_status()),
                "network": dict(node.get_network_status()),
                "peers": [dict(peer) for peer in node.get_peer_information()],
                "tips": [dict(tip) for tip in node.get_chain_tips()],
            }
        )
        return Diagnosis(
            classification=Classification.SUSPICIOUS,
            confidence=0.95,
            summary="Synthetic degradation was investigated through the read-only adapter.",
            evidence=[
                Evidence(
                    source="network_status",
                    observation="The synthetic fixture reports a deterministic degradation.",
                    significance="The condition warrants one bounded investigation.",
                ),
                Evidence(
                    source="chain_tips",
                    observation="The synthetic active chain tip remained readable.",
                    significance="The endpoint remained available for safe observation.",
                ),
            ],
            safety_boundary="Read-only synthetic observation; no remediation was performed.",
        )


def _node(url: str) -> CoreRpcNodeAdapter:
    return CoreRpcNodeAdapter(
        JsonRpcHttpTransport(
            url=url,
            username=TEST_RPC_USERNAME,
            password=TEST_RPC_PASSWORD,
            timeout_seconds=1,
        )
    )


def run_acceptance() -> dict[str, Any]:
    """Run the transition sequence through real HTTP with no paid provider calls."""
    controller = ScenarioController()
    harness = SyntheticRpcHarness(controller=controller, port=0)
    provider = FakeDiagnosisProvider()
    states: list[str] = []
    harness.start()
    url = harness.url
    node = _node(url)
    monitor = MonitoringService(
        snapshot_source=lambda: evaluate_health(node),
        diagnosis_runner=lambda: diagnose(node, provider),
    )
    # Acceptance tooling advances cycles directly; production timing remains unchanged.
    monitor._active = True
    try:
        for scenario in (
            "healthy",
            "degraded_peer_connectivity",
            "degraded_peer_connectivity",
            "degraded_header_gap",
            "healthy",
        ):
            controller.set(scenario)
            monitor.run_cycle()
            state = monitor.status.current_state
            states.append(state.value if state else "unknown")
    finally:
        harness.stop()
    monitor.run_cycle()
    unavailable = monitor.status.current_state
    states.append(unavailable.value if unavailable else "unknown")
    monitor.stop(wait=False)

    serialized_observations = json.dumps(provider.observations, sort_keys=True)
    forbidden = ("addr", "addrbind", "subver", "mapped_as", "localaddresses", "proxy")
    privacy_clean = all(token not in serialized_observations for token in forbidden)
    return {
        "states": states,
        "provider_invocations": provider.invocations,
        "privacy_clean": privacy_clean,
        "events": [event.message for event in monitor.status.events],
    }


def _serve(port: int, scenario_file: Path, initial_scenario: str) -> None:
    controller = ScenarioController(initial_scenario, scenario_file)
    harness = SyntheticRpcHarness(controller=controller, port=port)
    harness.start()
    print(f"Synthetic Core RPC: {harness.url}")
    print(f"Test username: {TEST_RPC_USERNAME}")
    print(f"Test password: {TEST_RPC_PASSWORD}")
    print(f"Scenario file: {scenario_file}")
    print(f"Scenarios: {', '.join(SCENARIO_NAMES)}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        harness.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="run the loopback synthetic RPC endpoint")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--scenario", choices=SCENARIO_NAMES, default="healthy")
    serve.add_argument("--scenario-file", type=Path, default=DEFAULT_SCENARIO_FILE)
    subparsers.add_parser("acceptance", help="run the cost-free transition acceptance sequence")
    args = parser.parse_args()
    if args.command == "acceptance":
        print(json.dumps(run_acceptance(), indent=2))
    else:
        _serve(args.port, args.scenario_file, args.scenario)


if __name__ == "__main__":
    main()
