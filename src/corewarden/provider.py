"""Provider-neutral model execution contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from corewarden.models import Diagnosis
from corewarden.node import CoreNode


@runtime_checkable
class DiagnosisProvider(Protocol):
    """Execute one diagnostic investigation with a configured model provider."""

    def diagnose(
        self,
        node: CoreNode,
        *,
        system_prompt: str,
        investigation_prompt: str,
    ) -> Diagnosis:
        """Use the node's fixed read-only capabilities to return a validated diagnosis."""
