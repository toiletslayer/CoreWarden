"""Current Strands and Amazon Bedrock diagnosis provider."""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass

from strands import Agent
from strands.models import BedrockModel

from corewarden.errors import ProviderError
from corewarden.models import Diagnosis
from corewarden.node import CoreNode
from corewarden.tools import create_diagnostic_tools

logger = logging.getLogger("corewarden.bedrock")

DEFAULT_BEDROCK_MODEL_MAX_TOKENS = 4096
DEFAULT_BEDROCK_MAX_TURNS = 6
DEFAULT_BEDROCK_MAX_OUTPUT_TOKENS = 12_000
DEFAULT_BEDROCK_MAX_TOTAL_TOKENS = 64_000
DEFAULT_BEDROCK_TIMEOUT_SECONDS = 120.0

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9._:/-]{1,256}$")
_SAFETY_LIMIT_STOP_REASONS = frozenset(
    {"cancelled", "limit_turns", "limit_output_tokens", "limit_total_tokens", "max_tokens"}
)
_SAFE_FAILURE_DETAILS = {
    "MissingDependencyException": "Required AWS SDK dependency is unavailable.",
    "NoCredentialsError": "AWS credentials were not available to boto3.",
    "PartialCredentialsError": "The AWS credential source was incomplete.",
    "ProfileNotFound": "The configured AWS profile was not found.",
    "LoginTokenLoadError": "The AWS login-session token could not be loaded.",
    "LoginRefreshRequired": "The AWS login session requires reauthentication.",
    "LoginInsufficientPermissions": "The AWS login session could not refresh credentials.",
}


def _finite_number(value: object) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


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
    model_max_tokens: int = DEFAULT_BEDROCK_MODEL_MAX_TOKENS
    max_turns: int = DEFAULT_BEDROCK_MAX_TURNS
    max_output_tokens: int = DEFAULT_BEDROCK_MAX_OUTPUT_TOKENS
    max_total_tokens: int = DEFAULT_BEDROCK_MAX_TOTAL_TOKENS
    timeout_seconds: float = DEFAULT_BEDROCK_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("Bedrock model ID cannot be empty")
        for label, value in (
            ("Bedrock model max tokens", self.model_max_tokens),
            ("Bedrock maximum turns", self.max_turns),
            ("Bedrock maximum output tokens", self.max_output_tokens),
            ("Bedrock maximum total tokens", self.max_total_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        if self.model_max_tokens > self.max_output_tokens:
            raise ValueError("Bedrock model max tokens cannot exceed the output-token budget")
        if self.max_output_tokens > self.max_total_tokens:
            raise ValueError("Bedrock output-token budget cannot exceed the total-token budget")
        if (
            not _finite_number(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 600
        ):
            raise ValueError("Bedrock timeout must be greater than 0 and at most 600 seconds")

    def diagnose(
        self,
        node: CoreNode,
        *,
        system_prompt: str,
        investigation_prompt: str,
    ) -> Diagnosis:
        cancel_signal = threading.Event()
        deadline = threading.Timer(self.timeout_seconds, cancel_signal.set)
        deadline.name = "corewarden-bedrock-deadline"
        deadline.daemon = True
        deadline.start()
        phase = "agent_construction"
        try:
            model = BedrockModel(model_id=self.model_id, max_tokens=self.model_max_tokens)
            agent = Agent(
                model=model,
                system_prompt=system_prompt,
                tools=create_diagnostic_tools(node),
                callback_handler=None,
            )
            phase = "agent_invocation"
            result = agent(
                investigation_prompt,
                structured_output_model=Diagnosis,
                limits={
                    "turns": self.max_turns,
                    "output_tokens": self.max_output_tokens,
                    "total_tokens": self.max_total_tokens,
                },
                cancel_signal=cancel_signal,
            )
        except Exception as exc:
            _log_provider_failure(exc, phase=phase, model_id=self.model_id)
            if cancel_signal.is_set():
                raise ProviderError(
                    "Bedrock investigation exceeded its configured time limit"
                ) from None
            raise ProviderError(
                "Bedrock provider invocation failed; check AWS credentials, model access, "
                "region, and diagnostic logs."
            ) from None
        finally:
            deadline.cancel()
            deadline.join()
        stop_reason = getattr(result, "stop_reason", None)
        if cancel_signal.is_set() or stop_reason in _SAFETY_LIMIT_STOP_REASONS:
            raise ProviderError("Bedrock investigation stopped at its configured safety limit")
        structured = getattr(result, "structured_output", None)
        if not isinstance(structured, Diagnosis):
            raise ProviderError("Bedrock returned no validated CoreWarden diagnosis")
        return structured
