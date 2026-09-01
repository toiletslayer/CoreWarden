# Synthetic monitoring acceptance harness

This development-only harness provides a loopback JSON-RPC endpoint for safe CoreWarden monitoring
tests and demonstrations. It is not imported, packaged, or started by the production application.
It supports only `getblockchaininfo`, `getnetworkinfo`, `getpeerinfo`, and `getchaintips`.

## Cost-free automated acceptance

From an installed development checkout, run:

```powershell
python scripts\synthetic_rpc_harness.py acceptance
```

The command starts an ephemeral loopback server, sends observations through CoreWarden's real HTTP
transport and privacy-filtering adapter, and advances these scenarios without waiting five minutes:

```text
healthy
degraded_peer_connectivity
degraded_peer_connectivity (unchanged; deduplicated)
degraded_header_gap (materially changed; one new investigation)
healthy (recovery; no investigation)
unavailable (server stopped; no investigation)
```

The investigation implementation is a fake `DiagnosisProvider`; it invokes the same four sanitized
node capabilities but makes no OpenAI or Bedrock request. Production monitoring intervals and GUI
behavior are unchanged.

## Interactive local server

Start the server in healthy mode:

```powershell
python scripts\synthetic_rpc_harness.py serve
```

The default endpoint and deliberately fake credentials are:

```text
RPC URL:  http://127.0.0.1:18443
Username: corewarden-test
Password: test-only-password
```

The server cannot be configured to bind publicly. Point a development or packaged CoreWarden GUI
at those values, start monitoring, and confirm the immediate state is Healthy with the last AI
investigation still showing Never.

The server reads its current scenario from the printed scenario-file path. On Windows, switch it
while the server is running with:

```powershell
$scenarioFile = Join-Path ([IO.Path]::GetTempPath()) 'corewarden-synthetic-scenario.txt'
Set-Content -LiteralPath $scenarioFile -Value 'degraded_peer_connectivity'
```

Supported file values are:

- `healthy`
- `degraded_peer_connectivity`
- `degraded_header_gap`
- `degraded_warning`
- `recovered` (the same coherent observations as healthy)

Use `Set-Content` again to change conditions. Normal GUI monitoring retains its five-minute minimum;
the automated acceptance command is the fast development-only path. Stop the server with Ctrl+C.
Deleting the temporary scenario file restores the command-line `--scenario` value on the next RPC
call.

All fixture addresses use documentation-only ranges and all credentials are test-only. Never copy
real node credentials, AWS credentials, or API keys into this harness.
