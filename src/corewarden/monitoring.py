"""Local-first, event-driven supervision for one Core-compatible node."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from corewarden.models import Diagnosis
from corewarden.node import CoreNode

DEFAULT_MONITORING_INTERVAL_SECONDS = 5 * 60
SUPPORTED_MONITORING_INTERVAL_MINUTES = (5, 10, 15, 30, 60)
DEFAULT_HISTORY_LIMIT = 20


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    state: HealthState
    reasons: tuple[str, ...]
    fingerprint: str
    checked_at: datetime
    normalized: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MonitoringEvent:
    occurred_at: datetime
    message: str
    state: HealthState | None = None
    event_type: str = "health"
    reasons: tuple[str, ...] = ()
    fingerprint_category: str | None = None
    provider: str | None = None
    classification: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class MonitoringStatus:
    active: bool
    current_state: HealthState | None
    last_check_at: datetime | None
    last_ai_at: datetime | None
    last_ai_status: str
    events: tuple[MonitoringEvent, ...]


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _warning(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return False


def _fingerprint(state: HealthState, signals: Mapping[str, Any]) -> str:
    document = json.dumps(
        {"state": state.value, "signals": signals}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def evaluate_health(node: CoreNode, *, now: Callable[[], datetime] | None = None) -> HealthSnapshot:
    """Collect four sanitized observations and classify them without model access."""
    checked_at = (now or (lambda: datetime.now(timezone.utc)))()
    try:
        blockchain = node.get_blockchain_status()
    except Exception:
        signals = {"rpc": "unavailable"}
        return HealthSnapshot(
            HealthState.UNAVAILABLE,
            ("Node RPC is unavailable",),
            _fingerprint(HealthState.UNAVAILABLE, signals),
            checked_at,
            signals,
        )

    failures: list[str] = []

    def collect(name: str, operation: Callable[[], Any], fallback: Any) -> Any:
        try:
            return operation()
        except Exception:
            failures.append(name)
            return fallback

    network = collect("network status", node.get_network_status, {})
    peers = collect("peer information", node.get_peer_information, ())
    tips = collect("chain tips", node.get_chain_tips, ())
    if not isinstance(blockchain, Mapping):
        blockchain = {}
        failures.append("blockchain status")
    if not isinstance(network, Mapping):
        network = {}
        failures.append("network status")
    if not isinstance(peers, Sequence) or isinstance(peers, str | bytes):
        peers = ()
        failures.append("peer information")
    if not isinstance(tips, Sequence) or isinstance(tips, str | bytes):
        tips = ()
        failures.append("chain tips")

    reasons = [f"Incomplete {name}" for name in sorted(set(failures))]
    blocks = _number(blockchain.get("blocks"))
    headers = _number(blockchain.get("headers"))
    progress = _number(blockchain.get("verificationprogress"))
    connections = _number(network.get("connections"))
    network_active = network.get("networkactive")

    if blocks is None or headers is None:
        reasons.append("Block or header height is unavailable")
    elif headers > blocks:
        reasons.append("Local blocks trail known headers")
    if blockchain.get("initialblockdownload") is True:
        reasons.append("Initial block download is active")
    if progress is not None and progress < 0.999:
        reasons.append("Verification progress is incomplete")
    if _warning(blockchain.get("warnings")) or _warning(network.get("warnings")):
        reasons.append("Node reports warnings")
    if network_active is False:
        reasons.append("Node networking is inactive")
    if connections is None:
        reasons.append("Connection count is unavailable")
    elif connections <= 0:
        reasons.append("No peer connections")
    if not peers:
        reasons.append("No peer health observations")

    active_tips = [
        tip
        for tip in tips
        if isinstance(tip, Mapping)
        and tip.get("status") == "active"
        and _number(tip.get("branchlen")) == 0
    ]
    if not active_tips:
        reasons.append("No active chain tip was reported")
    elif blocks is not None and all(_number(tip.get("height")) != blocks for tip in active_tips):
        reasons.append("Active chain-tip height differs from local blocks")

    peer_heights = sorted(
        {
            int(value)
            for peer in peers
            if isinstance(peer, Mapping)
            for value in (peer.get("synced_blocks"),)
            if _number(value) is not None
        }
    )
    state = HealthState.DEGRADED if reasons else HealthState.HEALTHY
    signals = {
        "reasons": sorted(set(reasons)),
        "blocks": int(blocks) if blocks is not None else None,
        "headers": int(headers) if headers is not None else None,
        "networkactive": network_active if type(network_active) is bool else None,
        "connections": int(connections) if connections is not None else None,
        "peer_count": len(peers),
        "peer_height_min": peer_heights[0] if peer_heights else None,
        "peer_height_max": peer_heights[-1] if peer_heights else None,
        "active_tip_heights": sorted(
            int(height)
            for tip in active_tips
            for height in (tip.get("height"),)
            if _number(height) is not None
        ),
    }
    condition_signals = {
        "reasons": signals["reasons"],
        "height_gap": (
            max(0, int(headers - blocks)) if blocks is not None and headers is not None else None
        ),
        "networkactive": signals["networkactive"],
        "connection_condition": "none" if connections is not None and connections <= 0 else "some",
        "active_tip_condition": "present" if active_tips else "missing",
    }
    return HealthSnapshot(
        state,
        tuple(sorted(set(reasons))),
        _fingerprint(state, condition_signals),
        checked_at,
        signals,
    )


@dataclass(slots=True)
class MonitoringService:
    """Run non-overlapping local checks and escalate changed degradation once."""

    snapshot_source: Callable[[], HealthSnapshot]
    diagnosis_runner: Callable[[], Diagnosis]
    interval_seconds: float = DEFAULT_MONITORING_INTERVAL_SECONDS
    history_limit: int = DEFAULT_HISTORY_LIMIT
    status_callback: Callable[[MonitoringStatus], None] | None = field(default=None, repr=False)
    event_callback: Callable[[MonitoringEvent], None] | None = field(default=None, repr=False)
    provider_name: str | None = None
    _events: deque[MonitoringEvent] = field(init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _cycle_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _active: bool = field(default=False, init=False)
    _snapshot: HealthSnapshot | None = field(default=None, init=False, repr=False)
    _last_ai_at: datetime | None = field(default=None, init=False, repr=False)
    _last_ai_status: str = field(default="Never", init=False)
    _investigated_fingerprints: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.interval_seconds < 60:
            raise ValueError("Monitoring interval must be at least 60 seconds")
        if self.history_limit < 1:
            raise ValueError("Monitoring history limit must be at least 1")
        self._events = deque(maxlen=self.history_limit)

    @property
    def status(self) -> MonitoringStatus:
        with self._lock:
            return MonitoringStatus(
                active=self._active,
                current_state=self._snapshot.state if self._snapshot else None,
                last_check_at=self._snapshot.checked_at if self._snapshot else None,
                last_ai_at=self._last_ai_at,
                last_ai_status=self._last_ai_status,
                events=tuple(self._events),
            )

    def _publish(self) -> None:
        callback = self.status_callback
        if callback is not None:
            with suppress(Exception):
                callback(self.status)

    def _record(
        self,
        message: str,
        state: HealthState | None = None,
        *,
        event_type: str = "health",
        reasons: tuple[str, ...] = (),
        fingerprint_category: str | None = None,
        provider: str | None = None,
        classification: str | None = None,
        confidence: float | None = None,
    ) -> None:
        event = MonitoringEvent(
            datetime.now(timezone.utc),
            message,
            state,
            event_type,
            reasons,
            fingerprint_category,
            provider,
            classification,
            confidence,
        )
        with self._lock:
            self._events.append(event)
        callback = self.event_callback
        if callback is not None:
            with suppress(Exception):
                callback(event)

    def start(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._stop_event.clear()
            self._record("Monitoring started", event_type="monitoring_started")
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="corewarden-monitor"
            )
            self._thread.start()
        self._publish()
        return True

    def stop(self, *, wait: bool = True, timeout: float | None = 5.0) -> bool:
        with self._lock:
            if not self._active:
                return False
            self._active = False
            self._stop_event.set()
            thread = self._thread
            self._record("Monitoring stopped", event_type="monitoring_stopped")
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._publish()
        return True

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_cycle()
            if self._stop_event.wait(self.interval_seconds):
                break

    def run_cycle(self) -> bool:
        """Run one cycle; return false when stopped or another cycle is in progress."""
        if not self._active or not self._cycle_lock.acquire(blocking=False):
            return False
        try:
            try:
                snapshot = self.snapshot_source()
            except Exception:
                signals = {"rpc": "unavailable"}
                snapshot = HealthSnapshot(
                    HealthState.UNAVAILABLE,
                    ("Node RPC is unavailable",),
                    _fingerprint(HealthState.UNAVAILABLE, signals),
                    datetime.now(timezone.utc),
                    signals,
                )
            with self._lock:
                previous = self._snapshot
                self._snapshot = snapshot
            changed = previous is None or snapshot.fingerprint != previous.fingerprint
            recovered = (
                previous is not None
                and previous.state is not HealthState.HEALTHY
                and snapshot.state is HealthState.HEALTHY
            )
            if recovered:
                self._record(
                    "Node recovered",
                    snapshot.state,
                    event_type="recovery",
                    fingerprint_category=snapshot.fingerprint,
                )
            elif (
                previous is None
                or previous.state is not snapshot.state
                or (snapshot.state is HealthState.DEGRADED and changed)
            ):
                detail = "; ".join(snapshot.reasons)
                message = snapshot.state.value.title()
                event_type = {
                    HealthState.HEALTHY: "health",
                    HealthState.DEGRADED: "degradation",
                    HealthState.UNAVAILABLE: "unavailable",
                }[snapshot.state]
                self._record(
                    f"{message}: {detail}" if detail else message,
                    snapshot.state,
                    event_type=event_type,
                    reasons=snapshot.reasons,
                    fingerprint_category=snapshot.fingerprint,
                )

            should_investigate = (
                snapshot.state is HealthState.DEGRADED
                and changed
                and snapshot.fingerprint not in self._investigated_fingerprints
                and not self._stop_event.is_set()
            )
            if should_investigate:
                self._investigated_fingerprints.add(snapshot.fingerprint)
                self._last_ai_at = datetime.now(timezone.utc)
                self._record(
                    "AI investigation started",
                    snapshot.state,
                    event_type="investigation_started",
                    reasons=snapshot.reasons,
                    fingerprint_category=snapshot.fingerprint,
                    provider=self.provider_name,
                )
                try:
                    diagnosis = self.diagnosis_runner()
                except Exception:
                    self._last_ai_status = "Failed"
                    self._record(
                        "AI investigation failed; deterministic monitoring continues",
                        snapshot.state,
                        event_type="investigation_failed",
                        provider=self.provider_name,
                    )
                else:
                    self._last_ai_status = (
                        f"{diagnosis.classification.value} ({diagnosis.confidence:.0%})"
                    )
                    self._record(
                        f"AI investigation: {self._last_ai_status}",
                        snapshot.state,
                        event_type="investigation_completed",
                        provider=self.provider_name,
                        classification=diagnosis.classification.value,
                        confidence=diagnosis.confidence,
                    )
            self._publish()
            return True
        finally:
            self._cycle_lock.release()
