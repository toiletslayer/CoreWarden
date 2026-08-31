"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from os import environ

from pydantic import ValidationError

from corewarden.agent import diagnose
from corewarden.bedrock import StrandsBedrockProvider
from corewarden.config import Settings
from corewarden.diagnostics import (
    EvidenceRecorder,
    SecretRedactor,
    configure_diagnostic_logging,
)
from corewarden.errors import ConfigurationError, CoreWardenError
from corewarden.openai_provider import OpenAIResponsesProvider
from corewarden.provider import DiagnosisProvider
from corewarden.rpc import CoreRpcNodeAdapter, JsonRpcHttpTransport


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Core-compatible node diagnosis")
    parser.add_argument(
        "--provider",
        choices=("bedrock", "openai"),
        default="bedrock",
        help="model provider to invoke explicitly (default: bedrock)",
    )
    return parser.parse_args(argv)


def select_provider(
    name: str,
    settings: Settings,
    env: Mapping[str, str] | None = None,
) -> tuple[DiagnosisProvider, str | None]:
    """Build exactly the selected provider without auto-detection or fallback."""
    if name == "bedrock":
        return StrandsBedrockProvider(settings.model_id), None
    if name != "openai":
        raise ConfigurationError(f"Unsupported provider: {name}")
    values = environ if env is None else env
    api_key = values.get("OPENAI_API_KEY")
    if not api_key:
        raise ConfigurationError("OPENAI_API_KEY is required when --provider openai is selected")
    return OpenAIResponsesProvider(api_key=api_key), api_key


def main(argv: Sequence[str] | None = None) -> int:
    recorder: EvidenceRecorder | None = None
    redactor = SecretRedactor()
    report = None
    error: dict[str, str] | None = None
    try:
        args = _parse_args(sys.argv[1:] if argv is None else argv)
        settings = Settings.from_env()
        provider, provider_secret = select_provider(args.provider, settings)
        redactor = SecretRedactor.from_values(
            settings.rpc_user, settings.rpc_password, provider_secret
        )
        configure_diagnostic_logging(settings.diagnostic_mode)
        transport = JsonRpcHttpTransport(
            url=settings.rpc_url,
            username=settings.rpc_user,
            password=settings.rpc_password,
            timeout_seconds=settings.rpc_timeout_seconds,
        )
        node = CoreRpcNodeAdapter(transport)
        if settings.diagnostic_mode:
            recorder = EvidenceRecorder(node, settings.evidence_path, redactor)
            node = recorder
        report = diagnose(node, provider)
    except (CoreWardenError, ValidationError, RuntimeError) as exc:
        error = {"error": type(exc).__name__, "message": redactor.text(str(exc))}
    except Exception as exc:
        logging.getLogger("corewarden").debug("Unexpected failure type: %s", type(exc).__name__)
        error = {
            "error": type(exc).__name__,
            "message": "CoreWarden failed unexpectedly; check configuration and diagnostic logs.",
        }

    if recorder is not None:
        try:
            recorder.write(report, error)
        except OSError as exc:
            error = {
                "error": type(exc).__name__,
                "message": "Could not write the configured diagnostic evidence file.",
            }

    if error is not None:
        print(json.dumps(redactor.redact(error)), file=sys.stderr)
        return 2
    if report is None:
        print(json.dumps({"error": "RuntimeError", "message": "No diagnosis was produced"}))
        return 2
    print(json.dumps(redactor.redact(report.model_dump(mode="json")), indent=2))
    return 0
