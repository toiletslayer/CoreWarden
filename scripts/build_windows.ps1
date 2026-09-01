$ErrorActionPreference = "Stop"

function Assert-NativeCommand {
    param([string]$Description)
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    foreach ($relativePath in @("build", "dist", "release")) {
        $target = [IO.Path]::GetFullPath((Join-Path $projectRoot $relativePath))
        $expectedParent = [IO.Path]::GetFullPath($projectRoot) + [IO.Path]::DirectorySeparatorChar
        if (-not $target.StartsWith($expectedParent, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a path outside the project: $target"
        }
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }

    python scripts\build_icon.py
    Assert-NativeCommand "Icon build"

    python -c "import corewarden, PyInstaller"
    if ($LASTEXITCODE -ne 0) {
        python -m pip install -e ".[package]"
        Assert-NativeCommand "Package dependency installation"
    }

    python -m PyInstaller --noconfirm --clean CoreWarden.spec
    Assert-NativeCommand "PyInstaller build"
    $executable = Join-Path $projectRoot "dist\CoreWarden\CoreWarden.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Expected packaged executable was not created: $executable"
    }

    python scripts\build_release.py
    Assert-NativeCommand "Release ZIP build"
    $releaseZip = Join-Path $projectRoot "release\CoreWarden-Windows-x64.zip"
    if (-not (Test-Path -LiteralPath $releaseZip -PathType Leaf)) {
        throw "Expected release ZIP was not created: $releaseZip"
    }
    Write-Host "Built: $executable"
    Write-Host "Release: $releaseZip"
}
finally {
    Pop-Location
}
