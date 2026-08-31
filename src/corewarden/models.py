"""Validated report schema returned by the Strands agent."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Classification(str, Enum):
    HEALTHY = "healthy"
    SUSPICIOUS = "suspicious"
    LIKELY_FAULT = "likely_fault"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal[
        "blockchain_status", "network_status", "peer_information", "chain_tips", "rpc_error"
    ]
    observation: str = Field(min_length=1, description="A concrete fact returned by a tool")
    significance: str = Field(min_length=1, description="Why the fact affects the diagnosis")


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Classification
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=2)
    uncertainties: list[str] = Field(default_factory=list)
    recommended_human_checks: list[str] = Field(default_factory=list)
    safety_boundary: str = Field(
        description="Confirm that no remediation or state-changing operation was performed"
    )
