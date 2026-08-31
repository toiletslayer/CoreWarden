import pytest
from pydantic import ValidationError

from corewarden.models import Diagnosis


def test_diagnosis_requires_correlated_evidence() -> None:
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(
            {
                "classification": "likely_fault",
                "confidence": 0.8,
                "summary": "Only one observation was supplied.",
                "evidence": [
                    {
                        "source": "rpc_error",
                        "observation": "one call failed",
                        "significance": "the endpoint may be degraded",
                    }
                ],
                "safety_boundary": "No changes made.",
            }
        )


def test_diagnosis_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(
            {
                "classification": "healthy",
                "confidence": 1,
                "summary": "ok",
                "evidence": [
                    {"source": "network_status", "observation": "a", "significance": "b"},
                    {"source": "chain_tips", "observation": "c", "significance": "d"},
                ],
                "safety_boundary": "No changes made.",
                "remediation_performed": True,
            }
        )
