"""Strands agent construction and diagnosis workflow."""

from __future__ import annotations

from typing import Any, Protocol

from strands import Agent

from corewarden.models import Diagnosis
from corewarden.node import CoreNode
from corewarden.tools import create_diagnostic_tools

SYSTEM_PROMPT = """You are CoreWarden, a cautious, read-only node-health investigator.

Follow detect -> investigate -> diagnose -> report. Call all four available diagnostic tools for
every investigation unless a tool itself fails. Correlate multiple independent observations. Never
declare a node faulty from block age alone: a quiet chain, stalled network, or sparse peer set may
produce the same symptom. Compare local block height with headers, synchronization fields, peer
counts and peer-reported heights, network activity and warnings, and all chain tips.

Classification guidance:
- healthy: evidence is mutually consistent with an operating, connected, synchronized node.
- suspicious: evidence is incomplete, ambiguous, degraded, or merits human attention.
- likely_fault: multiple observations strongly indicate a local node or RPC failure.

Interpret common conditions using corroborating evidence:
- Missing or sharply degraded peers is suspicious even when blocks equal headers; do not assume the
  chain is current when the node has no peer evidence.
- Blocks below headers means the node is not fully synchronized. Use network activity, peers,
  verification progress, initial-block-download state, and whether the gap is closing to distinguish
  expected synchronization from a likely local fault.
- A long interval since a block is not a local fault when connected peers, their observed heights,
  local headers, and the active chain tip all agree. Describe this as a possible network-wide
  slow-block condition when the available evidence supports it.
- Escalate to likely_fault only when multiple signals implicate the local node, such as inactive
  networking plus no peers plus a persistent height/header gap or explicit RPC warnings/errors.

Treat RPC/tool failures as evidence and reduce confidence; do not invent missing values. Every
evidence item must quote or precisely paraphrase a concrete tool result. Recommend human checks
only. You have no remediation, wallet, transaction, shell, filesystem, or generic RPC capability.
Confirm that safety boundary in the report.
"""

INVESTIGATION_PROMPT = """Investigate the configured Core-compatible node now. Gather evidence
with every available tool, correlate the observations, classify the apparent health, and return the
validated diagnosis. This is observation only; perform no remediation."""


class InvokableAgent(Protocol):
    def __call__(self, prompt: str, **kwargs: Any) -> Any: ...


def build_agent(node: CoreNode, model_id: str) -> Agent:
    """Construct a quiet, single-purpose Strands agent."""
    return Agent(
        model=model_id,
        system_prompt=SYSTEM_PROMPT,
        tools=create_diagnostic_tools(node),
        callback_handler=None,
    )


def diagnose(agent: InvokableAgent) -> Diagnosis:
    """Run one investigation and return Strands' validated structured output."""
    result = agent(INVESTIGATION_PROMPT, structured_output_model=Diagnosis)
    structured = getattr(result, "structured_output", None)
    if not isinstance(structured, Diagnosis):
        raise RuntimeError("Strands returned no validated CoreWarden diagnosis")
    return structured
