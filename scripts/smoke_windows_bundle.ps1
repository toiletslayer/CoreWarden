[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BundlePath,
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$expectedTitle = "CoreWarden — Read-only Node Health"
$resolved = (Resolve-Path -LiteralPath $BundlePath).Path
$executable = if (Test-Path -LiteralPath $resolved -PathType Container) {
    Join-Path $resolved "CoreWarden.exe"
} else {
    $resolved
}
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "CoreWarden.exe was not found at: $executable"
}

$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $executable
$startInfo.WorkingDirectory = Split-Path -Parent $executable
$startInfo.UseShellExecute = $false
$startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
foreach ($name in @(
    "OPENAI_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE", "COREWARDEN_RPC_USERNAME",
    "COREWARDEN_RPC_PASSWORD", "COREWARDEN_RPC_COOKIE_FILE"
)) {
    [void]$startInfo.EnvironmentVariables.Remove($name)
}

$process = [Diagnostics.Process]::new()
$process.StartInfo = $startInfo
$forcedCleanup = $false
$started = $false
try {
    if (-not $process.Start()) {
        throw "Windows did not start $executable"
    }
    $started = $true
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        $process.Refresh()
        if ($process.HasExited) {
            throw "CoreWarden exited before its main window opened (exit code $($process.ExitCode))."
        }
    } until (
        ($process.MainWindowHandle -ne 0 -and
            -not [string]::IsNullOrWhiteSpace($process.MainWindowTitle)) -or
        [DateTime]::UtcNow -ge $deadline
    )

    $process.Refresh()
    if ($process.MainWindowTitle -match "Unhandled exception|Traceback|Error") {
        throw "CoreWarden opened an error window: $($process.MainWindowTitle)"
    }
    if ($process.MainWindowTitle -ne $expectedTitle) {
        throw "Expected window title '$expectedTitle'; observed '$($process.MainWindowTitle)'."
    }
    if (-not $process.Responding) {
        throw "CoreWarden's main window is not responding."
    }

    Write-Host "PASS: CoreWarden launched and responded with the expected main window."
}
finally {
    if ($started -and -not $process.HasExited) {
        [void]$process.CloseMainWindow()
        if (-not $process.WaitForExit(5000)) {
            $forcedCleanup = $true
            $process.Kill()
            [void]$process.WaitForExit(5000)
        }
    }
    $process.Dispose()
}
if ($forcedCleanup) {
    throw "CoreWarden passed startup checks but did not close cleanly; exact-process cleanup was required."
}
