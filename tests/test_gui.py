from __future__ import annotations

from corewarden.desktop import DesktopRunResult
from corewarden.gui import format_diagnosis
from tests.test_agent import sample_diagnosis


def test_format_diagnosis_shows_only_readable_structured_fields() -> None:
    text = format_diagnosis(DesktopRunResult(provider="openai", diagnosis=sample_diagnosis()))

    assert "Provider: OpenAI" in text
    assert "Classification: healthy" in text
    assert "Confidence: 90%" in text
    assert "Evidence:" in text
    assert "no remediation" in text
