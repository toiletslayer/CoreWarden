"""Current Strands and Amazon Bedrock diagnosis provider."""

from __future__ import annotations

from dataclasses import dataclass

from strands import Agent

from corewarden.errors import ProviderError
from corewarden.models import Diagnosis
from corewarden.node import CoreNode
from corewarden.tools import create_diagnostic_tools


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
            result = agent(investigation_prompt, structured_output_model=Diagnosis)
        except Exception:
            raise ProviderError(
                "Bedrock provider invocation failed; check AWS credentials, model access, "
                "region, and diagnostic logs."
            ) from None
        structured = getattr(result, "structured_output", None)
        if not isinstance(structured, Diagnosis):
            raise ProviderError("Bedrock returned no validated CoreWarden diagnosis")
        return structured
