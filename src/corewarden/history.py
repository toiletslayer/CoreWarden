"""Durable, allow-listed local monitoring history and exports."""

from __future__ import annotations

import csv
import json
import os
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any

HISTORY_SCHEMA_VERSION = 1
HISTORY_RETENTION_LIMIT = 1000
APPLICATION_DIRECTORY_NAME = "CoreWarden"
HISTORY_RELATIVE_PATH = Path("history") / "monitoring-history.json"
PREFERENCES_FILENAME = "preferences.json"

EVENT_TYPES = frozenset(
    {
        "monitoring_started",
        "monitoring_stopped",
        "health",
        "degradation",
        "unavailable",
        "recovery",
        "investigation_started",
        "investigation_completed",
        "investigation_failed",
    }
)
HEALTH_STATES = frozenset({"healthy", "degraded", "unavailable"})
PROVIDERS = frozenset({"OpenAI", "Amazon Bedrock / Strands"})
CLASSIFICATIONS = frozenset({"healthy", "suspicious", "likely_fault"})
SAFE_HEALTH_REASONS = frozenset(
    {
        "Incomplete blockchain status",
        "Incomplete network status",
        "Incomplete peer information",
        "Incomplete chain tips",
        "Block or header height is unavailable",
        "Local blocks trail known headers",
        "Initial block download is active",
        "Verification progress is incomplete",
        "Node reports warnings",
        "Node networking is inactive",
        "Connection count is unavailable",
        "No peer connections",
        "No peer health observations",
        "No active chain tip was reported",
        "Active chain-tip height differs from local blocks",
        "Node RPC is unavailable",
    }
)
SAFE_EVENT_DEFAULTS = {
    "monitoring_started": "Monitoring started",
    "monitoring_stopped": "Monitoring stopped",
    "health": "Healthy",
    "degradation": "Degraded condition detected",
    "unavailable": "Node RPC is unavailable",
    "recovery": "Node recovered",
    "investigation_started": "Meaningful degradation triggered investigation",
    "investigation_completed": "Investigation completed",
    "investigation_failed": "Provider invocation failed; deterministic monitoring continues",
}


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validated_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 30:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return utc_timestamp(parsed)


def local_timestamp_values(value: str, local_timezone: tzinfo | None = None) -> tuple[str, str]:
    """Return a derived local ISO timestamp and unambiguous UTC-offset label."""
    canonical = validated_timestamp(value)
    if canonical is None:
        raise ValueError("Invalid canonical UTC timestamp")
    parsed = datetime.fromisoformat(canonical[:-1] + "+00:00")
    local = parsed.astimezone(local_timezone)
    offset = local.utcoffset()
    if offset is None:
        raise ValueError("Local timezone has no UTC offset")
    total_minutes = round(offset.total_seconds() / 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return local.isoformat(timespec="seconds"), f"UTC{sign}{hours:02d}:{minutes:02d}"


def local_data_directory(
    environment: Mapping[str, str] | None = None, *, platform: str | None = None
) -> Path:
    """Return the non-roaming application-data directory without network access."""
    values = os.environ if environment is None else environment
    selected_platform = os.name if platform is None else platform
    if selected_platform == "nt":
        base = values.get("LOCALAPPDATA")
        if base:
            return Path(base) / APPLICATION_DIRECTORY_NAME
        return Path.home() / "AppData" / "Local" / APPLICATION_DIRECTORY_NAME
    base = values.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "corewarden"


def default_history_path(environment: Mapping[str, str] | None = None) -> Path:
    return local_data_directory(environment) / HISTORY_RELATIVE_PATH


def default_preferences_path(environment: Mapping[str, str] | None = None) -> Path:
    return local_data_directory(environment) / PREFERENCES_FILENAME


def controlled_reason(event_type: str, reasons: tuple[str, ...] = ()) -> str:
    """Build history text exclusively from CoreWarden-owned reason constants."""
    safe = sorted({reason for reason in reasons if reason in SAFE_HEALTH_REASONS})
    if safe:
        return "; ".join(safe)
    return SAFE_EVENT_DEFAULTS[event_type]


def is_controlled_reason(value: str) -> bool:
    if value in SAFE_EVENT_DEFAULTS.values():
        return True
    parts = value.split("; ")
    return bool(parts) and all(part in SAFE_HEALTH_REASONS for part in parts)


def safe_fingerprint_category(value: str | None, state: str | None) -> str | None:
    if value and len(value) == 64 and all(character in "0123456789abcdef" for character in value):
        return value
    return state if state in HEALTH_STATES else None


@dataclass(frozen=True, slots=True)
class SanitizedHistoryEvent:
    """Explicit persisted schema; arbitrary source fields have nowhere to go."""

    timestamp: str
    event_type: str
    state: str | None
    reason: str
    fingerprint_category: str | None = None
    investigation_occurred: bool = False
    provider: str | None = None
    classification: str | None = None
    confidence: float | None = None
    provider_failure_category: str | None = None
    recovery: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SanitizedHistoryEvent | None:
        event_type = value.get("event_type")
        timestamp = validated_timestamp(value.get("timestamp"))
        reason = value.get("reason")
        if event_type not in EVENT_TYPES or not isinstance(timestamp, str) or not timestamp:
            return None
        if (
            not isinstance(reason, str)
            or not reason
            or len(reason) > 500
            or not is_controlled_reason(reason)
        ):
            return None
        state = value.get("state") if value.get("state") in HEALTH_STATES else None
        provider = value.get("provider") if value.get("provider") in PROVIDERS else None
        classification = (
            value.get("classification") if value.get("classification") in CLASSIFICATIONS else None
        )
        confidence_value = value.get("confidence")
        confidence = (
            float(confidence_value)
            if isinstance(confidence_value, int | float)
            and not isinstance(confidence_value, bool)
            and 0 <= confidence_value <= 1
            else None
        )
        failure = (
            "provider_invocation_failed"
            if value.get("provider_failure_category") == "provider_invocation_failed"
            else None
        )
        fingerprint = safe_fingerprint_category(value.get("fingerprint_category"), state)
        return cls(
            timestamp=timestamp,
            event_type=event_type,
            state=state,
            reason=reason,
            fingerprint_category=fingerprint,
            investigation_occurred=bool(value.get("investigation_occurred", False)),
            provider=provider,
            classification=classification,
            confidence=confidence,
            provider_failure_category=failure,
            recovery=event_type == "recovery" and bool(value.get("recovery", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def persisted_event_from_monitoring(event: Any) -> SanitizedHistoryEvent | None:
    """Project one typed monitoring event into the strict persisted schema."""
    event_type = getattr(event, "event_type", None)
    if event_type not in EVENT_TYPES:
        return None
    state_value = getattr(event, "state", None)
    state = getattr(state_value, "value", None)
    if state not in HEALTH_STATES:
        state = None
    provider = getattr(event, "provider", None)
    if provider not in PROVIDERS:
        provider = None
    classification = getattr(event, "classification", None)
    if classification not in CLASSIFICATIONS:
        classification = None
    confidence = getattr(event, "confidence", None)
    if (
        not isinstance(confidence, int | float)
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        confidence = None
    reasons = getattr(event, "reasons", ())
    if not isinstance(reasons, tuple):
        reasons = ()
    return SanitizedHistoryEvent(
        timestamp=utc_timestamp(getattr(event, "occurred_at", None)),
        event_type=event_type,
        state=state,
        reason=controlled_reason(event_type, reasons),
        fingerprint_category=safe_fingerprint_category(
            getattr(event, "fingerprint_category", None), state
        ),
        investigation_occurred=event_type.startswith("investigation_"),
        provider=provider,
        classification=classification,
        confidence=float(confidence) if confidence is not None else None,
        provider_failure_category=(
            "provider_invocation_failed" if event_type == "investigation_failed" else None
        ),
        recovery=event_type == "recovery",
    )


class HistoryStore:
    """Thread-safe fixed-cap JSON history with atomic replacement."""

    def __init__(self, path: Path, *, retention_limit: int = HISTORY_RETENTION_LIMIT) -> None:
        if retention_limit < 1:
            raise ValueError("History retention limit must be at least 1")
        self.path = path
        self.retention_limit = retention_limit
        self.warning: str | None = None
        self._lock = threading.RLock()
        self._events: list[SanitizedHistoryEvent] = []
        self._corrupt = False
        self._load()

    def _load(self) -> None:
        try:
            exists = self.path.exists()
        except OSError:
            self.warning = "Saved history could not be read; monitoring can still start."
            return
        if not exists:
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(document, dict)
                or document.get("schema_version") != HISTORY_SCHEMA_VERSION
            ):
                raise ValueError("invalid schema")
            raw_events = document.get("events", [])
            if not isinstance(raw_events, list):
                raise ValueError("invalid events")
            events = [
                event
                for item in raw_events
                if isinstance(item, dict)
                for event in (SanitizedHistoryEvent.from_mapping(item),)
                if event is not None
            ]
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            self.warning = "Saved history could not be read; the existing file was left unchanged."
            self._corrupt = True
            return
        self._events = events[-self.retention_limit :]

    def events(self) -> tuple[SanitizedHistoryEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def append(self, event: SanitizedHistoryEvent) -> bool:
        with self._lock:
            validated = SanitizedHistoryEvent.from_mapping(event.to_dict())
            if validated is None:
                self.warning = "An invalid history event was not saved."
                return False
            original = list(self._events)
            self._events.append(validated)
            self._events = self._events[-self.retention_limit :]
            try:
                self._save()
            except OSError:
                self._events = original
                self.warning = "Monitoring continues, but local history could not be saved."
                return False
            self.warning = None
            return True

    def _preserve_corrupt_file(self) -> None:
        if not self._corrupt or not self.path.exists():
            return
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        preserved = self.path.with_name(f"{self.path.name}.corrupt-{suffix}")
        self.path.replace(preserved)
        self._corrupt = False

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._preserve_corrupt_file()
        document = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "retention_limit": self.retention_limit,
            "events": [event.to_dict() for event in self._events],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def export_json(self, destination: Path) -> None:
        with self._lock:
            document = {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "exported_at": utc_timestamp(),
                "event_count": len(self._events),
                "retention_limit": self.retention_limit,
                "description": "Sanitized local CoreWarden monitoring history",
                "events": [event.to_dict() for event in self._events],
            }
        self._atomic_text(destination, json.dumps(document, indent=2, sort_keys=True) + "\n")

    def export_csv(self, destination: Path, local_timezone: tzinfo | None = None) -> None:
        event_fields = tuple(SanitizedHistoryEvent.__dataclass_fields__)
        fields = ("timestamp", "timestamp_local", "timezone", *event_fields[1:])
        with self._lock:
            rows = []
            for event in self._events:
                timestamp_local, timezone_label = local_timestamp_values(
                    event.timestamp, local_timezone
                )
                rows.append(
                    event.to_dict()
                    | {
                        "timestamp_local": timestamp_local,
                        "timezone": timezone_label,
                    }
                )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temporary, destination)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _atomic_text(destination: Path, content: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, destination)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise


class LocalPreferences:
    """Tiny non-sensitive preference file for bounded UI notices."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def tray_notice_shown(self) -> bool:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(document, dict) and document.get("tray_notice_shown") is True

    def mark_tray_notice_shown(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text('{"tray_notice_shown": true}\n', encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            return
