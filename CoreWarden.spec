"""PyInstaller one-folder build for the CoreWarden Windows desktop app."""

from pathlib import Path

project_root = Path(SPECPATH)
icon_path = project_root / "assets" / "corewarden.ico"
datas = []
if icon_path.exists():
    datas.append((str(icon_path), "assets"))

analysis = Analysis(
    [str(project_root / "src" / "corewarden" / "gui.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=["strands.models.bedrock"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="CoreWarden",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)

bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CoreWarden",
)
