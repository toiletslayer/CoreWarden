$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    python -m pip install -e ".[package]"
    python -m PyInstaller --noconfirm --clean CoreWarden.spec
    Write-Host "Built: $projectRoot\dist\CoreWarden\CoreWarden.exe"
}
finally {
    Pop-Location
}
