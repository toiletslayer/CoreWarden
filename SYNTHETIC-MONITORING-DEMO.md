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

## Short hackathon demo sequence

For a short video, keep the story in three parts:

1. Show the packaged GUI monitoring an authorized healthy node. Point out `Healthy`, the last-check
   time, and `Last AI investigation: Never`.
2. Run the cost-free `acceptance` command and briefly show healthy, changed degradation,
   deduplication, recovery, unavailability, and `privacy_clean: true` without waiting five minutes.
3. If one live Strands demonstration is desired, start the synthetic server in
   `degraded_peer_connectivity`, select Bedrock in the GUI, and start monitoring. The immediate
   degraded snapshot triggers one investigation. Stop monitoring after the result so the demo makes
   no later cycle. This optional step uses the operator's AWS account and incurs one Bedrock request;
   it must target the synthetic endpoint, not a production node.

End on the architecture diagram and the four-method allow-list. Use an authorized disposable node
or previously captured validation for real outage/recovery footage; do not interrupt a production
node solely for a demonstration.

## Successful Bedrock monitoring acceptance

The final real-provider acceptance used the loopback synthetic node with Strands, Amazon Bedrock in
`us-west-2`, and `global.anthropic.claude-sonnet-4-6`. The healthy cycle made no provider call.
Changing to `degraded_peer_connectivity` produced one monitoring-triggered investigation, invoked
all four sanitized tools, and returned `suspicious` with confidence `0.82`. A second cycle with the
same degradation was deduplicated, leaving the total at one escalation. The provider-visible
privacy audit passed; no retry, fallback, OpenAI request, remediation, or real-node contact occurred.
