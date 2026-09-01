"""Current Strands and Amazon Bedrock diagnosis provider."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from strands import Agent

from corewarden.errors import ProviderError
from corewarden.models import Diagnosis
from corewarden.node import CoreNode
from corewarden.tools import create_diagnostic_tools

logger = logging.getLogger("corewarden.bedrock")

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9._:/-]{1,256}$")
_SAFE_FAILURE_DETAILS = {
    "MissingDependencyException": "Required AWS SDK dependency is unavailable.",
    "NoCredentialsError": "AWS credentials were not available to boto3.",
    "PartialCredentialsError": "The AWS credential source was incomplete.",
    "ProfileNotFound": "The configured AWS profile was not found.",
    "LoginTokenLoadError": "The AWS login-session token could not be loaded.",
    "LoginRefreshRequired": "The AWS login session requires reauthentication.",
    "LoginInsufficientPermissions": "The AWS login session could not refresh credentials.",
}


def _safe_label(value: object, *, fallback: str = "unavailable") -> str:
    text = str(value) if value is not None else ""
    return text if _SAFE_LABEL.fullmatch(text) else fallback


def _log_provider_failure(exc: Exception, *, phase: str, model_id: str) -> None:
    """Log bounded provider metadata without serializing exception text or request data."""
    response = getattr(exc, "response", None)
    error = response.get("Error", {}) if isinstance(response, Mapping) else {}
    error_code = error.get("Code") if isinstance(error, Mapping) else None
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    exception_type = type(exc).__name__
    detail = _SAFE_FAILURE_DETAILS.get(
        exception_type, "Provider initialization or invocation failed."
    )
    logger.debug(
        "Bedrock provider failure | phase=%s service=bedrock-runtime region=%s "
        "model_id=%s exception=%s aws_error_code=%s operation=%s detail=%s",
        _safe_label(phase),
        _safe_label(region, fallback="default-chain"),
        _safe_label(model_id),
        _safe_label(exception_type),
        _safe_label(error_code),
        _safe_label(getattr(exc, "operation_name", None)),
        detail,
    )


@dataclass(frozen=True, slots=True)
class StrandsBedrockProvider:
    """Run CoreWarden's existing Strands agent with an Amazon Bedrock model."""

    model_id: str

    def diagnose(
        self,
        node: CoreNode,
        *,
        system_prompt: str,
        investigation_prompt: str,
    ) -> Diagnosis:
        try:
            agent = Agent(
                model=self.model_id,
                system_prompt=system_prompt,
                tools=create_diagnostic_tools(node),
                callback_handler=None,
            )
        except Exception as exc:
            _log_provider_failure(exc, phase="agent_construction", model_id=self.model_id)
            raise ProviderError(
                "Bedrock provider invocation failed; check AWS credentials, model access, "
                "region, and diagnostic logs."
            ) from None
        try:
            result = agent(investigation_prompt, structured_output_model=Diagnosis)
        except Exception as exc:
            _log_provider_failure(exc, phase="agent_invocation", model_id=self.model_id)
            raise ProviderError(
                "Bedrock provider invocation failed; check AWS credentials, model access, "
                "region, and diagnostic logs."
            ) from None
        structured = getattr(result, "structured_output", None)
        if not isinstance(structured, Diagnosis):
            raise ProviderError("Bedrock returned no validated CoreWarden diagnosis")
        return structured
