"""Command-line entry point."""

from __future__ import annotations

import json
import logging
import sys

from pydantic import ValidationError
from strands.types.exceptions import StructuredOutputException

from corewarden.agent import diagnose
from corewarden.bedrock import StrandsBedrockProvider
from corewarden.config import Settings
from corewarden.diagnostics import (
    EvidenceRecorder,
    SecretRedactor,
    configure_diagnostic_logging,
)
from corewarden.errors import CoreWardenError
from corewarden.rpc import CoreRpcNodeAdapter, JsonRpcHttpTransport


def main() -> int:
    recorder: EvidenceRecorder | None = None
    redactor = SecretRedactor()
    report = None
    error: dict[str, str] | None = None
    try:
        settings = Settings.from_env()
        redactor = SecretRedactor.from_values(settings.rpc_user, settings.rpc_password)
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
        report = diagnose(node, StrandsBedrockProvider(settings.model_id))
    except (CoreWardenError, StructuredOutputException, ValidationError, RuntimeError) as exc:
        error = {"error": type(exc).__name__, "message": redactor.text(str(exc))}
    except Exception as exc:  # SDK/provider exception classes vary between releases.
        logging.getLogger("corewarden").debug("Agent/provider failure type: %s", type(exc).__name__)
        error = {
            "error": type(exc).__name__,
            "message": "Agent or model-provider invocation failed; check AWS credentials, "
            "model access, region, and diagnostic logs.",
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
