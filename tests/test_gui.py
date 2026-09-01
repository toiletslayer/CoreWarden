from __future__ import annotations

from corewarden.desktop import DesktopRunResult
from corewarden.gui import FIRST_RUN_GUIDANCE, PRIVACY_NOTICE, format_diagnosis
from tests.test_agent import sample_diagnosis


def test_format_diagnosis_shows_only_readable_structured_fields() -> None:
    text = format_diagnosis(DesktopRunResult(provider="openai", diagnosis=sample_diagnosis()))

    assert "Provider: OpenAI" in text
    assert "Classification: healthy" in text
    assert "Confidence: 90%" in text
    assert "Evidence:" in text
    assert "no remediation" in text


def test_first_run_and_privacy_copy_describes_the_existing_safe_workflow() -> None:
    for step in ("Choose a provider", "Test Provider", "Test Node", "Run Diagnosis"):
        assert step in FIRST_RUN_GUIDANCE
    for method in (
        "getblockchaininfo",
        "getnetworkinfo",
        "getpeerinfo",
        "getchaintips",
    ):
        assert method in PRIVACY_NOTICE
    assert "filtered locally before model access" in PRIVACY_NOTICE
    assert "Windows Credential Manager" in PRIVACY_NOTICE
    assert "does not intentionally persist raw RPC credentials" in PRIVACY_NOTICE
