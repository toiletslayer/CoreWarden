"""PyInstaller one-folder build for the CoreWarden Windows desktop app."""

from pathlib import Path

project_root = Path(SPECPATH)
icon_path = project_root / "assets" / "corewarden.ico"
asset_paths = [
    icon_path,
    project_root / "assets" / "Sprite32.png",
    project_root / "assets" / "Sprite64.png",
    project_root / "assets" / "Sprite128.png",
]
missing_assets = [str(path) for path in asset_paths if not path.is_file()]
if missing_assets:
    raise FileNotFoundError(
        "Missing CoreWarden branding assets. Run python scripts/build_icon.py first: "
        + ", ".join(missing_assets)
    )
datas = [(str(path), "assets") for path in asset_paths]

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
    icon=str(icon_path),
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
