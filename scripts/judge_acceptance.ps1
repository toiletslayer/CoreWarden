[CmdletBinding()]
param(
    [string]$PythonExecutable = "python",
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$harness = Join-Path $PSScriptRoot "synthetic_rpc_harness.py"
Write-Host "CoreWarden cost-free judge acceptance"
Write-Host "Synthetic/fake acceptance provider: no OpenAI or Bedrock request."
Write-Host "No real node, provider credentials, or paid model network is used."

$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $PythonExecutable
$startInfo.Arguments = '"' + $harness + '" acceptance'
$startInfo.WorkingDirectory = $projectRoot
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
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
try {
    if (-not $process.Start()) {
        throw "Could not start Python acceptance process."
    }
    $stdoutRead = $process.StandardOutput.ReadToEndAsync()
    $stderrRead = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill()
        [void]$process.WaitForExit(5000)
        throw "Synthetic acceptance exceeded the $TimeoutSeconds-second limit."
    }
    $stdout = $stdoutRead.Result
    $stderr = $stderrRead.Result
    if ($process.ExitCode -ne 0) {
        throw "Synthetic acceptance failed with exit code $($process.ExitCode): $stderr"
    }
    $result = $stdout | ConvertFrom-Json
}
finally {
    $process.Dispose()
}

$expectedStates = @("healthy", "degraded", "degraded", "degraded", "healthy", "unavailable")
if (($result.states -join ",") -ne ($expectedStates -join ",")) {
    throw "Unexpected state sequence: $($result.states -join ', ')"
}
if ([int]$result.provider_invocations -ne 2) {
    throw "Expected exactly two fake-provider investigations; observed $($result.provider_invocations)."
}
if ($result.privacy_clean -ne $true) {
    throw "The provider-visible privacy assertion failed."
}
$events = @($result.events)
if (($events | Where-Object { $_ -eq "AI investigation started" }).Count -ne 2 -or
    ($events | Where-Object { $_ -match "^AI investigation:" }).Count -ne 2 -or
    ($events | Where-Object { $_ -eq "Node recovered" }).Count -ne 1 -or
    ($events | Where-Object { $_ -match "^Unavailable:" }).Count -ne 1) {
    throw "Monitoring event semantics did not match degradation, deduplication, recovery, and unavailability expectations."
}

Write-Host "PASS: healthy -> degraded -> deduplicated -> materially changed -> recovered -> unavailable"
Write-Host "PASS: exactly 2 fake-provider investigations"
Write-Host "PASS: provider-visible privacy assertions are clean"
