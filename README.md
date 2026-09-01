# CoreWarden

CoreWarden is a read-only, autonomous node-health investigator for Bitcoin
Core-compatible blockchain nodes. It is a milestone project for the AWS Agents
for Humans hackathon.

Point CoreWarden at one node and its selected model provider gathers several independent
observations, correlates them, and returns a validated JSON diagnosis:

- `healthy`
- `suspicious`
- `likely_fault`

CoreWarden does not repair the node. It explains what it observed and suggests
checks for a human operator.

## Validated status

Milestone 2 includes a successful live diagnosis of a Bitcoin II v31.1.0 node
through Strands and Amazon Bedrock in `us-west-2`, using
`global.anthropic.claude-sonnet-4-6`. All four fixed RPC tools succeeded and the
agent classified the synchronized node as `healthy` with confidence `0.93`.

The committed [live evidence artifact](corewarden-evidence-live-healthy-success.json)
contains the four sanitized observations and validated diagnosis. Its privacy
audit found no credentials, addresses, endpoint strings, hostnames, client
subversions, peer/session identifiers, AS mappings, proxy/listener endpoints, or
unknown peer fields in model payloads, logs, structured output, or evidence. No
node modification or remediation occurred. The associated test checkpoint is 28
passing tests, 93% coverage, and clean Ruff lint and changed-file formatting
checks. See [LIVE_VALIDATION.md](LIVE_VALIDATION.md) for details.

## Milestone 1 architecture and Milestone 2 validation

CoreWarden can read:

- blockchain status (`getblockchaininfo`)
- network status (`getnetworkinfo`)
- peer information (`getpeerinfo`)
- known chain tips (`getchaintips`)

The workflow is:

```text
detect -> investigate -> diagnose -> report
              |              |
              |              +-- validated Diagnosis JSON
              +-- four fixed, read-only RPC tools
```

The investigator is evidence-driven. For example, an old block timestamp by
itself is not proof of a broken local node. The agent also considers local block
and header heights, synchronization progress, peer connectivity and heights,
network state and warnings, and competing or invalid chain tips.

## Architecture

```text
CLI / environment
       |
       v
manual diagnosis / optional local monitor
                       |
                       +-- deterministic health snapshot + transition policy
                       |                         |
                       |                         +-- unchanged state: no AI
                       |                         +-- changed degradation: investigate once
                       v
provider-neutral diagnostic workflow
       |
       v
DiagnosisProvider protocol
       |                                   |
       v                                   v
StrandsBedrockProvider       OpenAIResponsesProvider
       |                                   |
       v                                   v
Strands Agent tools          Responses API function tools
       |                                   |
       +---------------+-------------------+
                       |
                       v
              Diagnosis (Pydantic)
       |
       v
CoreNode protocol
       |
       v
CoreRpcNodeAdapter (privacy projection)
       |
       v
JSON-RPC HTTP transport ----> one Core-compatible node
```

`CoreNode` is the chain-neutral diagnostic interface. `CoreRpcNodeAdapter` is
the first adapter and depends only on the conventional Bitcoin Core-style RPC
surface, not on a chain name, ticker, port, genesis block, expected block time,
or hard-coded height. A Bitcoin II node can therefore be an initial test target,
while other Bitcoin-derived Core nodes should work without changes when they
implement the same four RPC methods.

The adapter name describes the RPC family, not a dependency on the Bitcoin
mainnet. A future incompatible Core-derived RPC surface can implement `CoreNode`
without changing the tools or agent workflow.

`DiagnosisProvider` is the provider-neutral model execution boundary. The
diagnostic workflow supplies the fixed safety policy, investigation prompt, and
already privacy-filtered `CoreNode`; it does not import Strands or Bedrock.
`StrandsBedrockProvider` and `OpenAIResponsesProvider` implement the same
provider-neutral contract. Bedrock constructs the existing Strands tools and
agent. OpenAI uses the Responses API with exactly four parameterless function
tools and validates the final JSON against the same `Diagnosis` model. Provider
selection is explicit; there is no auto-detection, fallback, or routing between
providers. Provider-specific failures are normalized into safe CoreWarden errors
before they reach the CLI.

## Safety boundary

Milestone 1 is structurally read-only. Its RPC allow-list contains exactly:

```text
getblockchaininfo
getnetworkinfo
getpeerinfo
getchaintips
```

There is no generic RPC tool. The Strands agent cannot choose an RPC method and
cannot access the transport directly. It has no shell, filesystem, wallet,
transaction, restart, remediation, dashboard, deployment, or fleet tool.

CoreWarden must not and does not:

- access, export, or inspect private keys
- send transactions or move funds
- unlock wallets or change wallet settings
- restart a process or change node settings
- delete or prune blockchain data
- execute arbitrary shell commands
- make any destructive node change

Run it with a node RPC account restricted to these four methods when the node or
an RPC proxy supports method-level authorization. Defense in depth still matters:
LLM instructions and application allow-lists are not substitutes for endpoint
access controls.

Raw peer RPC results can contain identifying metadata, so the adapter projects
them onto an explicit health-only field allow-list before they reach Strands,
Bedrock, OpenAI, logs, structured evidence, or the evidence recorder. Continue treating
diagnostic artifacts as operational data even though addresses and identifiers
are excluded.

## Requirements

- Python 3.10 or newer
- one reachable Bitcoin Core-compatible JSON-RPC endpoint
- HTTP Basic Auth credentials if the endpoint requires them
- for Bedrock: AWS credentials through boto3's normal chain and model access
- for OpenAI: a user-supplied `OPENAI_API_KEY` with access to `gpt-5.6-luna`

CoreWarden is BYO-credential software. It never contains a shared/public API key.
The CLI reads the OpenAI key from the process environment only when
`--provider openai` is explicitly selected. The Windows desktop app can instead
store the key in the current user's Windows Credential Manager. Neither path
writes the key to application settings, logs, or diagnostic evidence.

## Setup

```bash
python -m venv .venv
```

Activate the environment:

```powershell
.venv\Scripts\Activate.ps1
```

```bash
source .venv/bin/activate
```

Install the application and test tools:

```bash
python -m pip install -e ".[dev]"
```

## Configuration

The CLI reads configuration from environment variables only. It does not parse
`.env` files itself, which avoids adding a second secret-loading mechanism.
`.env.example` is a copyable reference and `.env` is git-ignored. The desktop
app accepts settings in memory and persists only the OpenAI key, through Windows
Credential Manager rather than a plaintext file.

| Variable | Required | Default | Meaning |
|---|---:|---|---|
| `COREWARDEN_RPC_URL` | yes | — | Full `http://` or `https://` JSON-RPC URL |
| `COREWARDEN_RPC_USER` | no | — | RPC Basic Auth username |
| `COREWARDEN_RPC_PASSWORD` | no | — | RPC Basic Auth password |
| `COREWARDEN_RPC_TIMEOUT_SECONDS` | no | `10` | Per-call timeout, greater than 0 and at most 300 |
| `COREWARDEN_MODEL_ID` | no | `global.anthropic.claude-sonnet-4-6` | Bedrock model ID for Strands |
| `OPENAI_API_KEY` | OpenAI only | — | BYO OpenAI project key; never stored by CoreWarden |
| `COREWARDEN_DIAGNOSTIC_MODE` | no | `false` | Record redacted evidence and emit safe lifecycle debug logs |
| `COREWARDEN_EVIDENCE_PATH` | no | `corewarden-evidence.json` | Diagnostic evidence output path |

The RPC user and password must either both be set or both be absent. Credentials
embedded in the URL are rejected to reduce accidental disclosure in logs and
process listings.

AWS variables such as `AWS_REGION`, `AWS_PROFILE`, or temporary credential
variables follow boto3 behavior and are not read by CoreWarden itself.

PowerShell example:

```powershell
$env:COREWARDEN_RPC_URL = "http://127.0.0.1:8337"
$env:COREWARDEN_RPC_USER = "diagnostic-user"
$env:COREWARDEN_RPC_PASSWORD = "replace-me"
$env:AWS_REGION = "us-west-2"
$env:OPENAI_API_KEY = "your-project-key-here"
```

Bash example:

```bash
export COREWARDEN_RPC_URL="http://127.0.0.1:8337"
export COREWARDEN_RPC_USER="diagnostic-user"
export COREWARDEN_RPC_PASSWORD="replace-me"
export AWS_REGION="us-west-2"
export OPENAI_API_KEY="your-project-key-here"
```

Ports are deliberately not defaulted because derived chains commonly use
different RPC ports.

## Run

Select the provider explicitly. Bedrock remains the compatibility default, but
spelling out the provider is recommended for scripts and operator clarity:

```bash
corewarden --provider bedrock
corewarden --provider openai
```

or:

```bash
python -m corewarden
```

The OpenAI implementation uses exactly `gpt-5.6-luna` for this slice. It makes
Responses API calls with standard processing, low reasoning effort, response
storage disabled, and structured JSON Schema output. It does not auto-detect an
API key and never falls back to Bedrock. If `OPENAI_API_KEY` is missing, startup
fails before any node tool runs or evidence file is created.

## Windows desktop app

### Judge quickstart

1. Extract the entire `CoreWarden` folder from `CoreWarden-Windows-x64.zip`.
2. Launch `CoreWarden.exe`. The unsigned build may require confirmation through
   Windows SmartScreen.
3. Choose OpenAI or Bedrock. For OpenAI, paste a project key and select **Save
   securely**; for Bedrock, use an authenticated AWS profile/session with access
   in the selected region.
4. Enter the URL and authentication for a running local Core-compatible node.
   Use either RPC username/password or a cookie file.
5. Select **Test Provider**, **Test Node**, then **Run Diagnosis**.
6. Read the classification, confidence, evidence, and safety boundary in the
   result panel.

CoreWarden calls only `getblockchaininfo`, `getnetworkinfo`, `getpeerinfo`, and
`getchaintips`. Peer-identifying and endpoint data is filtered locally before
model access. The app stores OpenAI keys in Windows Credential Manager and does
not intentionally persist raw RPC credentials or peer-identifying observations.
The release ZIP also includes `JUDGE-QUICKSTART.txt` with these steps.

### Development launch

Install and launch the native desktop entry point during development:

```powershell
python -m pip install -e .
corewarden-gui
```

The small `tkinter` window provides:

- explicit OpenAI or Bedrock selection;
- RPC URL plus session-only username/password or generic RPC cookie-file input;
- secure OpenAI-key save/remove actions;
- separate provider and node configuration checks;
- read-only diagnosis through the existing `diagnose()` workflow;
- optional local monitoring at 5, 10, 15, 30, or 60-minute intervals;
- a concise classification, confidence, summary, evidence, and safety result.

### Optional local monitoring

Monitoring is off until the operator explicitly starts it. It performs one immediate check and
then checks at the selected interval (5 minutes by default). Each check calls only the same four
read-only methods through `CoreRpcNodeAdapter`, so peer and endpoint privacy filtering occurs
before the monitoring policy or a provider can receive observations.

The local policy reports `healthy`, `degraded`, or `unavailable`. It records a small in-memory
history of state transitions and meaningful condition changes. Healthy steady state never invokes
AI. A new or materially changed degraded fingerprint invokes the existing diagnosis workflow once;
the same unchanged problem, including a failed provider attempt, is deduplicated for the remainder
of that monitoring session. RPC unavailability is recorded locally without invoking AI, and
recovery is recorded without a recovery model call. Monitoring never retries a provider simply
because another timer cycle elapsed.

The monitor and model investigation run off the tkinter UI thread. Cycles do not overlap, duplicate
monitoring loops are rejected, and closing the window signals monitoring to stop. Event history is
bounded to 20 controlled, non-secret messages and is not persisted.

For a cost-free local transition demonstration using a loopback-only synthetic Core RPC endpoint,
see [SYNTHETIC-MONITORING-DEMO.md](SYNTHETIC-MONITORING-DEMO.md). The harness is development-only
and is not part of the packaged application or production runtime.

When an OpenAI key is saved, it is stored as a generic credential named
`CoreWarden/OpenAI` in the current Windows user's Credential Manager. The entry
field is cleared immediately and the full value is never displayed again. The
desktop app checks this credential first and retains `OPENAI_API_KEY` as a
power-user fallback. RPC credentials and cookie contents are held only for the
current process and are never saved by CoreWarden.

Bedrock uses the existing AWS CLI/profile/session credential chain. The desktop
provider check validates the selected AWS identity and creates the Bedrock
runtime path without storing AWS access keys. Actual model permission is
confirmed only when a diagnosis is run, avoiding a separate paid inference test.

OpenAI's provider check makes one small, tool-free `gpt-5.6-luna` Responses API
request with `store=False`. It does not contact the node or start the diagnostic
tool loop. The node check makes one allowed read-only blockchain-status call and
does not invoke a model.

### Build the Windows bundle

Build on Windows with the included PowerShell script:

```powershell
.\scripts\build_windows.ps1
```

The script rebuilds the approved multi-resolution icon, cleans prior build,
distribution, and release directories, installs the `package` optional
dependency, runs the checked-in PyInstaller specification, and creates the
judge-ready ZIP. Its outputs are:

```text
dist\CoreWarden\CoreWarden.exe
release\CoreWarden-Windows-x64.zip
```

Distribute the ZIP. It contains only the complete `CoreWarden` onedir bundle and
the short judge quickstart. The judge does not need a separate Python
installation, but still needs a reachable local node and either their own OpenAI
project key or an authenticated AWS profile/session. A one-folder build is used
instead of a one-file archive for predictable startup and dependency inspection.

The approved `Sprite32.png`, `Sprite64.png`, and `Sprite128.png` files are kept
unchanged under `assets`. `scripts\build_icon.py` embeds all three native PNG
payloads into `assets\corewarden.ico`; PyInstaller uses it for the executable and
the GUI uses the packaged artwork for its window and compact header branding.

Known limitations: this is a Windows-first local bundle, not an installer; it
does not create accounts, configure a node, persist RPC secrets, refresh expired
AWS sessions, sign the executable, or bypass Windows reputation warnings.

Successful output is one validated JSON document. Expected configuration, RPC,
and structured-output failures are emitted as JSON on stderr with exit code 2.
RPC error messages never include configured credentials.

When diagnostic mode is enabled, CoreWarden records the exact read-only evidence
seen by the agent plus the final diagnosis. A recursive redactor removes known
RPC credentials, username/password fields, authorization headers, Basic/Bearer
values, cookies, tokens, private-key fields, and credentials embedded in URLs.
Debug logs contain lifecycle events and tool names, never raw RPC requests,
headers, or responses. At the RPC adapter boundary, peer results are projected
onto health-only fields before reaching the tools, model, or recorder. Addresses,
hostnames, bound/local endpoints, client subversions, peer IDs, AS mappings, and
all unknown peer fields are discarded. Retained data is limited to connection
direction/type, synchronization heights, latency and activity timing, transfer
counters, capability tokens, and similarly non-identifying health metrics.

See [LIVE_VALIDATION.md](LIVE_VALIDATION.md) for the Bitcoin II healthy-node run,
expected output, evidence review, and the safe reversible controlled-fault test.

Example report shape (illustrative, not a real diagnosis):

```json
{
  "classification": "healthy",
  "confidence": 0.91,
  "summary": "The node appears connected and synchronized.",
  "evidence": [
    {
      "source": "blockchain_status",
      "observation": "blocks and headers are both 250000",
      "significance": "the local chain is not behind its known headers"
    },
    {
      "source": "peer_information",
      "observation": "8 peers are connected with comparable starting heights",
      "significance": "multiple peers corroborate network connectivity"
    }
  ],
  "uncertainties": [],
  "recommended_human_checks": [],
  "safety_boundary": "Read-only checks only; no remediation was performed."
}
```

## How provider tools work

The provider-neutral workflow calls a `DiagnosisProvider` with a `CoreNode` and
the fixed CoreWarden prompts. `StrandsBedrockProvider` calls
`create_diagnostic_tools()`, which closes over that node and decorates four
parameterless Python functions with Strands' `@tool`. Their docstrings tell the
model what evidence each tool supplies. Those four function tools are passed
directly in `Agent(tools=[...])`.

`OpenAIResponsesProvider` declares the same four semantics as strict,
parameterless Responses API function tools and dispatches them only through the
four `CoreNode` methods. A model-supplied arbitrary RPC or tool name is rejected;
there is no generic transport function. Sanitized results are returned as
`function_call_output` items until the model emits the shared structured
diagnosis. The provider permits at most six response/tool-loop iterations and
does not retry provider failures, preventing malformed responses from consuming
unbounded API usage.

The system prompt requires the agent to call all four, correlate evidence, avoid
single-metric conclusions, and report uncertainty. The invocation passes
`Diagnosis` through `structured_output_model`; Strands validates the model output
and exposes it as `AgentResult.structured_output`. Because the CLI constructs the
provider only after the Core-compatible adapter and optional evidence recorder,
the provider boundary cannot receive raw peer or local endpoint metadata.

## Tests

```bash
pytest
```

Ordinary tests never contact a node, Bedrock, or OpenAI. `FakeTransport` returns representative
mock JSON-RPC results keyed by the exact method name. This tests the adapter's
mapping and allow-list independently of HTTP. Separate mocked `urlopen` tests
exercise JSON decoding and RPC/HTTP error handling. A mocked Strands agent tests
the current structured-output invocation contract without making a model call.
Provider tests separately verify the provider-neutral workflow, Strands/Bedrock
construction, OpenAI Responses request shape, exact model selection, all four
function-call semantics, bounded loops, safe error normalization, explicit
provider selection, no fallback, and the sanitized data visible at both provider
boundaries.

## Deliberately deferred

Remediation, restarts, wallet functionality, general-purpose dashboards, cloud deployment, fleet
management, transaction sending, chain-specific policy thresholds, persistent
state beyond opt-in local evidence capture, and arbitrary RPC remain out of scope.
Hosted/shared inference, provider auto-routing, credential GUIs or storage,
Ollama, direct Anthropic integration, and automatic fallback also remain out of
scope.

## Development references

The implementation follows the current official Strands guidance:

- [Python quickstart](https://strandsagents.com/docs/user-guide/quickstart/python/)
- [Custom tools](https://strandsagents.com/docs/user-guide/concepts/tools/custom-tools/)
- [Structured output](https://strandsagents.com/docs/user-guide/concepts/agents/structured-output/)
- [Amazon Bedrock provider](https://strandsagents.com/docs/user-guide/concepts/model-providers/amazon-bedrock/)

The OpenAI provider follows the official OpenAI documentation:

- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)

## License

Apache-2.0. See `LICENSE`.
