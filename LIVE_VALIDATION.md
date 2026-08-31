# Milestone 2 live validation

This procedure validates CoreWarden against one real Bitcoin Core-compatible
node without adding or invoking any state-changing capability.

## Safety preflight

Before each run:

1. Confirm the target is a node you are authorized to diagnose. CoreWarden
   strips peer addresses and identifying metadata at the adapter boundary before
   sanitized peer-health metrics can reach the configured model provider.
2. Use a dedicated RPC identity restricted to `getblockchaininfo`,
   `getnetworkinfo`, `getpeerinfo`, and `getchaintips` when method-level RPC
   authorization or a local proxy is available.
3. Keep the RPC endpoint on loopback or a trusted network. Do not expose it to
   the public internet for this test.
4. Do not place credentials in the URL, command line, evidence filename, or a
   committed file.
5. Treat the evidence file as operational data even though peer addresses,
   endpoint strings, client identifiers, and unknown peer fields are removed.

CoreWarden's adapter rejects every RPC method outside the four-method allow-list.
Diagnostic mode records only the values returned through those same methods.

## Bitcoin II healthy-node validation

Bitcoin II uses the same four parameterless RPC calls required by the generic
adapter. The chain name, ticker, data directory, expected port, height, and block
interval remain unconfigured and are not hard-coded.

Set these in the shell that will run CoreWarden:

```powershell
$env:COREWARDEN_RPC_URL = "http://127.0.0.1:8337"
$env:COREWARDEN_RPC_USER = "<your-read-only-rpc-user>"
$env:COREWARDEN_RPC_PASSWORD = "<your-rpc-password>"
$env:AWS_REGION = "us-west-2"
$env:COREWARDEN_DIAGNOSTIC_MODE = "true"
$env:COREWARDEN_EVIDENCE_PATH = ".\corewarden-evidence-healthy.json"
```

If you use a non-default Bedrock model, also set:

```powershell
$env:COREWARDEN_MODEL_ID = "<bedrock-model-id>"
```

AWS authentication must be available through exactly one normal boto3 route,
for example an already authenticated AWS profile:

```powershell
$env:AWS_PROFILE = "<profile-name>"
```

or temporary AWS credential variables managed by your usual credential tooling.
Do not put AWS secrets in CoreWarden's `.env.example` or evidence file.

Run:

```powershell
python -m corewarden
```

Expected results:

- exit code `0`
- one JSON `Diagnosis` on stdout
- classification normally `healthy` when blocks and headers agree, initial
  block download is false, networking is active, peers corroborate the height,
  and the active chain tip is consistent
- at least two concrete evidence entries
- a safety statement confirming that no remediation occurred
- `corewarden-evidence-healthy.json` containing the four read-only observations
  and the same diagnosis
- lifecycle/tool-name debug messages on stderr, with no RPC username, password,
  URL userinfo, Basic/Bearer authorization value, or authorization header

The diagnosis is evidence-based, so an otherwise healthy node can legitimately
be `suspicious` when it has too little peer evidence or warnings. Do not change
the classifier merely to force a `healthy` demonstration.

Review the evidence file before sharing it. Search for the real username and a
non-secret fragment of the password locally; both must be absent. Never paste a
real secret into a shell history solely to perform this check.

## Successful live validation checkpoint

The Milestone 2 healthy-node run completed successfully against Bitcoin II
v31.1.0 using Strands with Amazon Bedrock in `us-west-2` and model
`global.anthropic.claude-sonnet-4-6`.

- Structured classification: `healthy`
- Confidence: `0.93`
- Successful tools: `getblockchaininfo`, `getnetworkinfo`, `getpeerinfo`, and
  `getchaintips`
- Tool observations: four, all with status `ok`
- Privacy audit: passed
- Safety result: read-only observation only; no node modification or remediation
- Test checkpoint: 28 passing tests, 93% coverage, clean Ruff lint, and clean
  changed-file formatting checks

The privacy audit covered the tool-equivalent model payloads, diagnostic logs,
structured output, and committed evidence. It found no AWS or RPC credentials,
peer addresses, local/bound addresses, hostnames, client subversions,
peer/session IDs, AS mappings, proxy/listener endpoints, or unknown peer fields.
The recorder wraps the sanitized adapter, so the peer and network observations in
the artifact are the same projected payloads returned to the Strands tools.

The reviewed artifact is
[`corewarden-evidence-live-healthy-success.json`](corewarden-evidence-live-healthy-success.json).
It has schema version 1, four sanitized observations, the validated diagnosis,
and no run error. Generated evidence remains ignored by default; this file is the
single intentional, privacy-audited checkpoint exception.

## Evidence scenarios

### Healthy synchronized node

Expected evidence is mutually consistent: `blocks == headers`, synchronization
is complete, networking is active, peers exist with compatible heights, and one
active chain tip has `branchlen == 0`.

### Missing or degraded peers

Zero peers or inactive networking is at least suspicious. Equal blocks and
headers do not prove freshness when the node has no external evidence. A local
fault becomes more likely only when other signals corroborate it, such as a
height/header gap or explicit warnings.

### Blocks trail headers

`blocks < headers` means synchronization is incomplete. Initial block download,
verification progress, peer availability, network activity, and peer-reported
heights help distinguish expected catch-up from a stuck or isolated local node.

### Possible network-wide slow block

An old latest block is not sufficient evidence of a local fault. When the local
block/header heights, active tip, and several connected peers all agree, the
available observations support a network-wide quiet/slow-block interpretation.
CoreWarden should report uncertainty because its one-node view is not a network
oracle.

## Safest controlled-fault procedure

Use a disposable or dedicated Bitcoin II test VM that is already synchronized.
For the cleanest test, provision it with separate management and peer-facing
network interfaces: RPC/CoreWarden/Bedrock remain reachable over the management
path while only the peer-facing interface is disconnected. Do not perform this
test on the only copy of a production node.

1. Capture a healthy baseline with diagnostic mode enabled.
2. Using the VM or hypervisor's network control—not CoreWarden—temporarily
   disconnect only the peer-facing adapter while keeping the management path,
   VM, node process, RPC endpoint, and Bedrock access running.
3. Wait until the node reports zero/degraded peers. Do not delete data, change
   wallet state, alter the node configuration, or restart it.
4. Write to a different evidence file and run CoreWarden again:

   ```powershell
   $env:COREWARDEN_EVIDENCE_PATH = ".\corewarden-evidence-isolated.json"
   python -m corewarden
   ```

5. Expect `suspicious` for an isolated but otherwise internally consistent node.
   `likely_fault` is appropriate only if multiple local-failure signals are
   present. The report should explicitly say that equal blocks/headers cannot
   establish freshness without peers.
6. Reconnect the VM's network adapter using the same external control.
7. Confirm peers return, then run a third diagnosis to a new evidence file. The
   node should return to a healthy-looking state once evidence is consistent.

This is safe and reversible at the environment boundary. CoreWarden neither
causes nor repairs the condition. Avoid OS firewall commands in the validation
script: they are easier to leave behind accidentally and would blur the product's
no-shell/no-remediation boundary.

## Optional header-gap observation

To observe `blocks < headers` without corrupting data, use a separate disposable
node that is naturally catching up and run CoreWarden during initial block
download. Do not manufacture this condition by deleting blocks, rewinding the
chain, invalidating blocks, or modifying the data directory.

## Validation record

For a hackathon demonstration, retain:

- the redacted healthy evidence file and diagnosis
- the redacted isolated-node evidence file and diagnosis
- the redacted recovery evidence file and diagnosis
- CoreWarden version and model ID
- test timestamps and a short note describing the external VM network action

Do not retain RPC or AWS credentials in the validation record.
