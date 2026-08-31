"""Secret-safe evidence recording for opt-in live validation."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from corewarden.models import Diagnosis
from corewarden.node import CoreNode, JsonObject

logger = logging.getLogger("corewarden.diagnostics")
T = TypeVar("T")

_SENSITIVE_KEYS = re.compile(
    r"^(?:authorization|proxy-authorization|cookie|password|passwd|private[_-]?key|"
    r"rpcpassword|rpcuser|secret|token|username|user)$",
    re.IGNORECASE,
)
_AUTH_VALUE = re.compile(r"\b(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_URL_USERINFO = re.compile(r"(https?://)[^/@\s:]+(?::[^/@\s]*)?@", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SecretRedactor:
    """Recursively redact secret-bearing keys, auth values, URL userinfo, and known secrets."""

    known_secrets: tuple[str, ...] = ()

    @classmethod
    def from_values(cls, *values: str | None) -> SecretRedactor:
        return cls(tuple(value for value in values if value))

    def text(self, value: str) -> str:
        redacted = _AUTH_VALUE.sub("[REDACTED AUTHORIZATION]", value)
        redacted = _URL_USERINFO.sub(r"\1[REDACTED]@", redacted)
        for secret in sorted(self.known_secrets, key=len, reverse=True):
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    def redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): (
                    "[REDACTED]" if _SENSITIVE_KEYS.match(str(key)) else self.redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


@dataclass(slots=True)
class EvidenceRecorder:
    """Capture exactly what the four read-only node methods return to the agent."""

    node: CoreNode
    output_path: Path
    redactor: SecretRedactor
    observations: list[dict[str, Any]] = field(default_factory=list)

    def _capture(self, source: str, operation: Callable[[], T]) -> T:
        logger.debug("Collecting read-only evidence: %s", source)
        try:
            result = operation()
        except Exception as exc:
            self.observations.append(
                {
                    "source": source,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": self.redactor.text(str(exc)),
                }
            )
            logger.debug("Read-only evidence call failed: %s", source)
            raise
        self.observations.append(
            {"source": source, "status": "ok", "data": self.redactor.redact(result)}
        )
        logger.debug("Collected read-only evidence: %s", source)
        return result

    def get_blockchain_status(self) -> JsonObject:
        return self._capture("blockchain_status", self.node.get_blockchain_status)

    def get_network_status(self) -> JsonObject:
        return self._capture("network_status", self.node.get_network_status)

    def get_peer_information(self) -> Sequence[JsonObject]:
        return self._capture("peer_information", self.node.get_peer_information)

    def get_chain_tips(self) -> Sequence[JsonObject]:
        return self._capture("chain_tips", self.node.get_chain_tips)

    def write(self, diagnosis: Diagnosis | None, run_error: Mapping[str, str] | None) -> None:
        document = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "mode": "read_only_diagnostic",
            "allowed_rpc_methods": [
                "getblockchaininfo",
                "getnetworkinfo",
                "getpeerinfo",
                "getchaintips",
            ],
            "observations": self.observations,
            "diagnosis": diagnosis.model_dump(mode="json") if diagnosis else None,
            "run_error": dict(run_error) if run_error else None,
        }
        safe_document = self.redactor.redact(document)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output_path.with_name(f".{self.output_path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(safe_document, stream, indent=2, sort_keys=True)
                stream.write("\n")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                logger.debug("Could not tighten evidence-file permissions on this platform")
            os.replace(temporary, self.output_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        logger.debug(
            "Wrote redacted evidence file: %s", self.redactor.text(str(self.output_path))
        )


def configure_diagnostic_logging(enabled: bool) -> None:
    """Log lifecycle and tool names only; raw evidence is written through the redactor."""
    if not enabled:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger("corewarden")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    root.propagate = False
