"""Environment-only application configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from os import environ
from pathlib import Path
from urllib.parse import urlsplit

from corewarden.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    rpc_url: str
    rpc_user: str | None = None
    rpc_password: str | None = None
    rpc_timeout_seconds: float = 10.0
    model_id: str = "global.anthropic.claude-sonnet-4-6"
    diagnostic_mode: bool = False
    evidence_path: Path = Path("corewarden-evidence.json")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        values = environ if env is None else env
        rpc_url = values.get("COREWARDEN_RPC_URL", "").strip()
        if not rpc_url:
            raise ConfigurationError("COREWARDEN_RPC_URL is required")

        parsed = urlsplit(rpc_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("COREWARDEN_RPC_URL must be an http:// or https:// URL")
        if parsed.username or parsed.password:
            raise ConfigurationError(
                "Do not embed RPC credentials in COREWARDEN_RPC_URL; use the credential variables"
            )

        user = values.get("COREWARDEN_RPC_USER") or None
        password = values.get("COREWARDEN_RPC_PASSWORD") or None
        if bool(user) != bool(password):
            raise ConfigurationError(
                "COREWARDEN_RPC_USER and COREWARDEN_RPC_PASSWORD must be set together"
            )

        timeout_text = values.get("COREWARDEN_RPC_TIMEOUT_SECONDS", "10")
        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ConfigurationError("COREWARDEN_RPC_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0 or timeout > 300:
            raise ConfigurationError(
                "COREWARDEN_RPC_TIMEOUT_SECONDS must be greater than 0 and at most 300"
            )

        model_id = values.get(
            "COREWARDEN_MODEL_ID", "global.anthropic.claude-sonnet-4-6"
        ).strip()
        if not model_id:
            raise ConfigurationError("COREWARDEN_MODEL_ID cannot be empty")

        diagnostic_text = values.get("COREWARDEN_DIAGNOSTIC_MODE", "false").strip().lower()
        if diagnostic_text not in {"true", "false", "1", "0", "yes", "no"}:
            raise ConfigurationError(
                "COREWARDEN_DIAGNOSTIC_MODE must be true/false, yes/no, or 1/0"
            )
        diagnostic_mode = diagnostic_text in {"true", "1", "yes"}

        evidence_text = values.get(
            "COREWARDEN_EVIDENCE_PATH", "corewarden-evidence.json"
        ).strip()
        if not evidence_text:
            raise ConfigurationError("COREWARDEN_EVIDENCE_PATH cannot be empty")

        return cls(
            rpc_url=rpc_url,
            rpc_user=user,
            rpc_password=password,
            rpc_timeout_seconds=timeout,
            model_id=model_id,
            diagnostic_mode=diagnostic_mode,
            evidence_path=Path(evidence_text),
        )
