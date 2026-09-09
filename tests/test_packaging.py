from __future__ import annotations

import hashlib
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

APPROVED_ASSET_HASHES = {
    "Sprite32.png": "c5cdd742c926a63094ab2364e2edd7cec4a24e9700358f09a3ea001c3a9fe26d",
    "Sprite64.png": "a2337017d35aea35c011f1590b5788f6860a9ec46f47e4c12b46aa7adad42485",
    "Sprite128.png": "84797a8f008386094e4e7b84db6dd5824c92bb56c019cdf32ec2ba7e459d35a3",
}


def test_windows_packaging_configuration_has_expected_safety_shape() -> None:
    root = Path(__file__).parents[1]
    spec = (root / "CoreWarden.spec").read_text(encoding="utf-8")
    script = (root / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    smoke = (root / "scripts" / "smoke_windows_bundle.ps1").read_text(encoding="utf-8")
    acceptance = (root / "scripts" / "judge_acceptance.ps1").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "windows-release-build.yml").read_text(
        encoding="utf-8"
    )
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert 'name="CoreWarden"' in spec
    assert "console=False" in spec
    assert "windows-version-info.txt" in spec
    assert "version=str(version_path)" in spec
    for asset in ("corewarden.ico", "Sprite32.png", "Sprite64.png", "Sprite128.png"):
        assert asset in spec
    assert "PyInstaller --noconfirm --clean CoreWarden.spec" in script
    assert "python scripts\\build_icon.py" in script
    assert "python scripts\\build_release.py" in script
    assert "CoreWarden-Windows-x64.zip" in script
    assert 'foreach ($relativePath in @("build", "dist", "release"))' in script
    assert 'Assert-NativeCommand "PyInstaller build"' in script
    assert 'Assert-NativeCommand "Release ZIP build"' in script
    assert 'if ($version.Trim() -ne "3.12.10")' in script
    assert smoke.isascii()
    assert '"CoreWarden "' in smoke
    assert "[char]0x2014" in smoke
    assert '" Read-only Node Health"' in smoke
    assert acceptance.isascii()
    assert "Unhandled exception|Traceback|Error" in smoke
    assert "CloseMainWindow" in smoke
    assert "$process.Kill()" in smoke
    assert 'python-version: "3.12.10"' in workflow
    assert "Smoke built bundle" in workflow
    assert "Smoke extracted release ZIP" in workflow
    assert "Expand-Archive" in workflow
    expected_states = (
        'expectedStates = @("healthy", "degraded", "degraded", "degraded", '
        '"healthy", "unavailable")'
    )
    assert expected_states in acceptance
    assert "provider_invocations" in acceptance
    assert "privacy_clean" in acceptance
    assert "Synthetic/fake acceptance provider" in acceptance
    assert 'corewarden-gui = "corewarden.gui:main"' in pyproject
    assert '"botocore[crt]>=1.43.63,<2"' in pyproject
    assert '"Pillow>=10,<13"' in pyproject
    assert '"pystray>=0.19.5,<1"' in pyproject
    assert '"pystray._win32"' in spec


def test_approved_branding_assets_are_unchanged_and_ico_embeds_native_pngs() -> None:
    root = Path(__file__).parents[1]
    assets = root / "assets"
    for name, expected_hash in APPROVED_ASSET_HASHES.items():
        assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == expected_hash

    icon = (assets / "corewarden.ico").read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", icon)
    assert (reserved, image_type, count) == (0, 1, 3)
    for index, size in enumerate((32, 64, 128)):
        width, height, colors, reserved_byte, planes, bits, length, offset = struct.unpack_from(
            "<BBBBHHII", icon, 6 + index * 16
        )
        assert (width, height, colors, reserved_byte, planes, bits) == (size, size, 0, 0, 1, 32)
        assert icon[offset : offset + length] == (assets / f"Sprite{size}.png").read_bytes()


def test_icon_builder_reproduces_the_checked_in_icon(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    output = tmp_path / "corewarden.ico"

    subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "build_icon.py"),
            "--assets-dir",
            str(root / "assets"),
            "--output",
            str(output),
        ],
        check=True,
    )

    assert output.read_bytes() == (root / "assets" / "corewarden.ico").read_bytes()


def test_release_zip_contains_bundle_and_public_release_documents(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    bundle = tmp_path / "bundle"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "CoreWarden.exe").write_bytes(b"MZ-test")
    (bundle / "_internal" / "runtime.dll").write_bytes(b"runtime")
    (bundle / ".env").write_text("OPENAI_API_KEY=not-shippable", encoding="utf-8")
    (bundle / "debug.log").write_text("not-shippable", encoding="utf-8")
    (bundle / "temporary-evidence.json").write_text("{}", encoding="utf-8")
    cache = bundle / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"cache")
    output = tmp_path / "CoreWarden-Windows-x64.zip"

    subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "build_release.py"),
            "--bundle",
            str(bundle),
            "--output",
            str(output),
            "--project-root",
            str(root),
        ],
        check=True,
    )

    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "CoreWarden/CoreWarden.exe",
            "CoreWarden/JUDGE-QUICKSTART.txt",
            "CoreWarden/LICENSE",
            "CoreWarden/README.md",
            "CoreWarden/SYNTHETIC-MONITORING-DEMO.md",
            "CoreWarden/THIRD-PARTY-NOTICES.md",
            "CoreWarden/_internal/runtime.dll",
            "CoreWarden/docs/AI-USAGE-CREDENTIALS-PRIVACY.md",
            "CoreWarden/docs/BITCOIN-II-RPC-SETUP.md",
            "CoreWarden/scripts/judge_acceptance.ps1",
            "CoreWarden/scripts/synthetic_rpc_harness.py",
        }
        assert all(info.date_time == (2020, 1, 1, 0, 0, 0) for info in archive.infolist())
