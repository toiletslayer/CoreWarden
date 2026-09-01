from __future__ import annotations

from pathlib import Path


def test_windows_packaging_configuration_has_expected_safety_shape() -> None:
    root = Path(__file__).parents[1]
    spec = (root / "CoreWarden.spec").read_text(encoding="utf-8")
    script = (root / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert 'name="CoreWarden"' in spec
    assert "console=False" in spec
    assert "assets" in spec and "corewarden.ico" in spec
    assert "PyInstaller --noconfirm --clean CoreWarden.spec" in script
    assert 'corewarden-gui = "corewarden.gui:main"' in pyproject
