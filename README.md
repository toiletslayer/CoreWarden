# CoreWarden

CoreWarden is a local-first, read-only monitor and AI-assisted diagnostic tool
for Bitcoin Core-compatible cryptocurrency nodes. It collects structured node
evidence, evaluates health changes locally, and invokes AI when deeper reasoning
is warranted. Bitcoin II is the initial/reference implementation and validated
node target; other compatible nodes need their own compatibility validation.

The provider-neutral diagnostic boundary supports OpenAI's Responses API and
Strands Agents with Amazon Bedrock. Provider selection is explicit, with no
automatic fallback. A Windows desktop application and package provide diagnosis,
optional monitoring, and sanitized local history.

Node/RPC evidence is authoritative; AI interprets that evidence rather than
controlling the node. CoreWarden observes four fixed RPC methods and cannot repair
the node, access wallets, send transactions, or change node state.

Privacy-sensitive peer and endpoint fields are removed by the local RPC adapter
before monitoring policy, Strands, Bedrock, OpenAI, logs, or evidence recording
can receive the observations.

## What it reports

The deterministic monitor reports `healthy`, `degraded`, or `unavailable`.
When a new or materially changed degradation warrants investigation, the selected
provider correlates the four read-only observations and returns a validated diagnosis:

- `healthy`
- `suspicious`
- `likely_fault`

Unchanged degradation is deduplicated, healthy steady state does not invoke AI,
and RPC unavailability does not create a provider retry storm.

## Validated status

- Live Strands/Bedrock diagnosis against Bitcoin II v31.1.0 in `us-west-2`
  using `global.anthropic.claude-sonnet-4-6`; all four tools succeeded.
- Live OpenAI diagnosis through the same privacy-filtered node boundary.
- Packaged monitoring against a real Bitcoin II node: healthy steady state,
  outage detection, and automatic recovery, with no AI call while healthy or
  unavailable.
- Cost-free synthetic acceptance covering degradation, deduplication, changed
  degradation, recovery, unavailability, and provider-visible privacy.
- 123 passing tests and 91% coverage at the recorded resilience checkpoint.

The committed [live evidence artifact](corewarden-evidence-live-healthy-success.json)
contains the four sanitized observations and validated diagnosis. Its privacy
audit found no credentials, addresses, endpoint strings, hostnames, client
subversions, peer/session identifiers, AS mappings, proxy/listener endpoints, or
unknown peer fields in model payloads, logs, structured output, or evidence. No
node modification or remediation occurred. See [LIVE_VALIDATION.md](LIVE_VALIDATION.md)
for the original live-validation record.

## Read-only observations

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

See [ARCHITECTURE.md](ARCHITECTURE.md) for the GitHub-renderable Mermaid
diagram, control flow, privacy boundary, monitoring escalation policy, and
credential boundaries.

`CoreNode` is the chain-neutral diagnostic interface. `CoreRpcNodeAdapter` is
the first adapter and depends only on the conventional Bitcoin Core-style RPC
surface, not on a chain name, ticker, port, genesis block, expected block time,
or hard-coded height. Bitcoin II is the current reference target. Other
Bitcoin-derived nodes may be compatible when they implement the same four RPC
methods and response semantics, but are not yet validated integrations.

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

CoreWarden is structurally read-only. Its RPC allow-list contains exactly:

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

For a Windows packaging environment, install both optional groups:

```powershell
python -m pip install -e ".[dev,package]"
```

## Configuration

The CLI reads configuration from environment variables only. It does not parse
`.env` files itself, which avoids adding a second secret-loading mechanism.
`.env.example` is a copyable reference and `.env` is git-ignored. The desktop
app accepts settings in memory and persists the OpenAI key through Windows
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
CoreWarden includes botocore's CRT support so the Python and packaged runtimes
can consume `login_session` credentials created by `aws login`. Authenticate
with your own AWS configuration and select the same profile and region in
CoreWarden; access keys and session tokens are never stored by the application.

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

The CLI requires an explicit RPC URL and has no default port. The Windows
desktop pre-fills `http://127.0.0.1:8337` for the Bitcoin II reference target,
unless `COREWARDEN_RPC_URL` overrides it. The examples above use that same target.
For another node or network, enter its actual RPC endpoint before testing or
monitoring; the application does not detect the chain or select its port.

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

### Desktop quickstart

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
The release ZIP also includes [JUDGE-QUICKSTART.txt](JUDGE-QUICKSTART.txt), retained
under its historical filename, with these steps.

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
- persistent sanitized local history with JSON and CSV export;
- Windows system-tray operation while monitoring is active;
- a concise classification, confidence, summary, evidence, and safety result.

### Optional local monitoring

Monitoring is off until the operator explicitly starts it. It performs one immediate check and
then checks at the selected interval (5 minutes by default). Each check calls only the same four
read-only methods through `CoreRpcNodeAdapter`, so peer and endpoint privacy filtering occurs
before the monitoring policy or a provider can receive observations.

The local policy reports `healthy`, `degraded`, or `unavailable`. It records state transitions,
meaningful condition changes, and controlled investigation outcomes in sanitized local history.
Healthy steady state never invokes
AI. A new or materially changed degraded fingerprint invokes the existing diagnosis workflow once;
the same unchanged problem, including a failed provider attempt, is deduplicated for the remainder
of that monitoring session. RPC unavailability is recorded locally without invoking AI, and
recovery is recorded without a recovery model call. Monitoring never retries a provider simply
because another timer cycle elapsed.

The monitor and model investigation run off the tkinter UI thread. Cycles do not overlap and
duplicate monitoring loops are rejected. The recent-event panel remains bounded to 20 entries.

### Persistent history, export, and background operation

The Windows app keeps the newest 1000 allow-listed monitoring events in:

```text
%LOCALAPPDATA%\CoreWarden\history\monitoring-history.json
```

Writes use atomic replacement. A corrupt file never prevents startup; CoreWarden leaves it intact
and preserves it with a `.corrupt-<timestamp>` suffix before the next successful write. The
**History** dialog reviews prior sessions without contacting a node or provider. User-triggered
exports are available as authoritative structured JSON and flattened CSV.

Event timestamps are stored canonically as UTC ISO-8601 values and are not rewritten when the
History dialog is opened. The dialog derives its human-readable date/time from the current Windows
system timezone and displays an explicit UTC offset. JSON exports retain the authoritative UTC
`timestamp`. CSV retains that UTC value and adds derived `timestamp_local` and `timezone` columns
for human review; those convenience columns are not part of the persisted event schema.

Persisted fields are limited to event time/type, health state, controlled reason, degradation
category, whether investigation occurred, selected provider, validated classification/confidence,
safe provider-failure category, and recovery status. CoreWarden never puts RPC/provider credentials,
authorization data, raw RPC payloads, peer addresses or identifiers, endpoints, hostnames, client
subversions, AS/proxy data, arbitrary exception strings, prompts, or raw model responses into this
history. History stays on the local machine; there is no telemetry or cloud history service.

When monitoring is active, closing the window hides CoreWarden to the Windows system tray and
monitoring continues. A one-time local notice explains this behavior. The tray menu can restore the
single existing window, start or stop the existing monitoring service, or **Quit CoreWarden**.
Explicit Quit stops monitoring, removes the tray icon, and exits. When monitoring is idle, closing
the window exits normally and does not force tray residency.

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

Close any `CoreWarden.exe` launched from the repository's `dist` directory before
rebuilding; Windows keeps bundled DLLs locked while that app is running.

The script rebuilds the approved multi-resolution icon, cleans prior build,
distribution, and release directories, installs the `package` optional
dependency, runs the checked-in PyInstaller specification, and creates the
judge-ready ZIP. Its outputs are:

```text
dist\CoreWarden\CoreWarden.exe
release\CoreWarden-Windows-x64.zip
```

Distribute the ZIP. It contains the complete `CoreWarden` onedir bundle, the
short judge quickstart, the Apache-2.0 project license, and direct-dependency
notices. The judge does not need a separate Python
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

The synthetic acceptance tests additionally use a real loopback HTTP server and
the production transport/adapter path. They use deliberately fake credentials
and a fake provider; no paid API or real node is contacted.

## Deliberately deferred

Remediation, restarts, wallet functionality, general-purpose dashboards, cloud deployment, fleet
management, transaction sending, chain-specific policy thresholds, and arbitrary RPC remain out of
scope.
Hosted/shared inference, provider auto-routing, additional credential storage
beyond the existing Windows OpenAI-key support, Ollama, direct Anthropic
integration, and automatic fallback also remain out of scope.

Follow-up: explicit node/network selection and chain-specific desktop defaults
need a separate design and compatibility validation. The current Bitcoin II
prefill is not a chain-neutral discovery mechanism.

## Contributing and project history

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, security
expectations, and proposing support for another Core-compatible node.

CoreWarden originated in the AWS Agents for Humans hackathon context. The
[hackathon documentation index](docs/hackathon/README.md) distinguishes preserved
submission assets and recording plans from ongoing project documentation and
useful technical validation records.

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

Apache-2.0. See [LICENSE](LICENSE). Direct runtime and packaging dependency
attributions are summarized in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
