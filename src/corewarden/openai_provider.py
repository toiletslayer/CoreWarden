"""OpenAI Responses API diagnosis provider."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from corewarden.errors import ProviderError
from corewarden.models import Diagnosis
from corewarden.node import CoreNode

OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_ITERATIONS = 6


class ResponsesClient(Protocol):
    responses: Any


def _create_openai_client(api_key: str) -> ResponsesClient:
    from openai import OpenAI

    return OpenAI(api_key=api_key)


def _strict_diagnosis_schema() -> dict[str, Any]:
    schema = deepcopy(Diagnosis.model_json_schema())

    def make_strict(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            properties = value.get("properties")
            if value.get("type") == "object" and isinstance(properties, dict):
                value["additionalProperties"] = False
                value["required"] = list(properties)
            for child in value.values():
                make_strict(child)
        elif isinstance(value, list):
            for child in value:
                make_strict(child)

    make_strict(schema)
    return schema


_EMPTY_PARAMETERS = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

OPENAI_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "name": "get_blockchain_status",
        "description": "Read local chain height, header height, sync progress, and warnings.",
        "parameters": _EMPTY_PARAMETERS,
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_network_status",
        "description": "Read sanitized network activity, connection counts, and warnings.",
        "parameters": _EMPTY_PARAMETERS,
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_peer_information",
        "description": "Read sanitized peer health, connectivity, latency, and heights.",
        "parameters": _EMPTY_PARAMETERS,
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_chain_tips",
        "description": "Read active, valid-fork, and invalid chain tips known locally.",
        "parameters": _EMPTY_PARAMETERS,
        "strict": True,
    },
)


def _item_value(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _safe_tool_failure(exc: Exception) -> dict[str, str]:
    return {
        "error": type(exc).__name__,
        "message": "The fixed read-only node tool failed; treat its evidence as unavailable.",
    }


@dataclass(frozen=True, slots=True)
class OpenAIResponsesProvider:
    """Investigate a sanitized CoreNode through bounded OpenAI function calls."""

    api_key: str = field(repr=False)
    model: str = OPENAI_MODEL
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    client_factory: Callable[[str], ResponsesClient] = field(
        default=_create_openai_client, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is required when the OpenAI provider is selected")
        if self.model != OPENAI_MODEL:
            raise ProviderError(f"OpenAI model must be {OPENAI_MODEL}")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")

    def diagnose(
        self,
        node: CoreNode,
        *,
        system_prompt: str,
        investigation_prompt: str,
    ) -> Diagnosis:
        handlers: dict[str, Callable[[], Any]] = {
            "get_blockchain_status": node.get_blockchain_status,
            "get_network_status": node.get_network_status,
            "get_peer_information": node.get_peer_information,
            "get_chain_tips": node.get_chain_tips,
        }
        try:
            client = self.client_factory(self.api_key)
        except Exception:
            raise ProviderError("OpenAI client initialization failed") from None

        input_items: list[Any] = [{"role": "user", "content": investigation_prompt}]
        for _ in range(self.max_iterations):
            try:
                response = client.responses.create(
                    model=self.model,
                    instructions=system_prompt,
                    input=input_items,
                    tools=[deepcopy(tool) for tool in OPENAI_TOOLS],
                    tool_choice="auto",
                    parallel_tool_calls=False,
                    reasoning={"effort": "low"},
                    max_output_tokens=4096,
                    include=["reasoning.encrypted_content"],
                    service_tier="default",
                    store=False,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "corewarden_diagnosis",
                            "strict": True,
                            "schema": _strict_diagnosis_schema(),
                        }
                    },
                )
            except Exception:
                raise ProviderError("OpenAI provider invocation failed") from None

            output = list(getattr(response, "output", ()))
            tool_calls = [item for item in output if _item_value(item, "type") == "function_call"]
            if not tool_calls:
                output_text = getattr(response, "output_text", None)
                if not isinstance(output_text, str) or not output_text.strip():
                    raise ProviderError("OpenAI returned no validated CoreWarden diagnosis")
                try:
                    return Diagnosis.model_validate_json(output_text)
                except ValidationError:
                    raise ProviderError("OpenAI returned an invalid CoreWarden diagnosis") from None

            input_items.extend(output)
            for call in tool_calls:
                name = _item_value(call, "name")
                call_id = _item_value(call, "call_id")
                arguments = _item_value(call, "arguments")
                if name not in handlers or not isinstance(call_id, str):
                    raise ProviderError("OpenAI requested a tool outside the fixed allow-list")
                try:
                    parsed_arguments = json.loads(arguments or "{}")
                except (TypeError, json.JSONDecodeError):
                    raise ProviderError("OpenAI supplied invalid tool arguments") from None
                if parsed_arguments != {}:
                    raise ProviderError("OpenAI supplied unsupported tool arguments")
                try:
                    result = handlers[name]()
                except Exception as exc:
                    result = _safe_tool_failure(exc)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, sort_keys=True, separators=(",", ":")),
                    }
                )

        raise ProviderError(f"OpenAI tool-call iteration limit reached ({self.max_iterations})")

    def test_configuration(self) -> None:
        """Make one small, tool-free request to validate OpenAI authentication and access."""
        try:
            client = self.client_factory(self.api_key)
            response = client.responses.create(
                model=self.model,
                input="Reply with exactly OK.",
                reasoning={"effort": "none"},
                max_output_tokens=16,
                service_tier="default",
                store=False,
            )
        except Exception:
            raise ProviderError(
                "OpenAI configuration test failed; check the saved key and project access."
            ) from None
        if getattr(response, "status", None) == "failed":
            raise ProviderError(
                "OpenAI configuration test failed; check the saved key and project access."
            )
