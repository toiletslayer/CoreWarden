# CoreWarden architecture

CoreWarden separates presentation, deterministic supervision, AI investigation,
and the raw node transport. The same privacy-filtered `CoreNode` interface is
used by manual diagnosis and monitoring escalation.

```mermaid
flowchart TB
    User[Human operator] --> GUI[Windows tkinter GUI]
    User --> CLI[CLI]
    GUI --> Desktop[DesktopService]
    Desktop --> Monitor[MonitoringService]
    Monitor --> Health[Local deterministic health evaluation]
    Monitor -- new or changed degradation only --> Workflow[diagnose workflow]
    Desktop -- manual diagnosis --> Workflow
    CLI --> Workflow

    Workflow --> Provider[DiagnosisProvider]
    Provider --> Bedrock[StrandsBedrockProvider]
    Provider --> OpenAI[OpenAIResponsesProvider]
    Bedrock --> StrandsTools[Four fixed Strands tools]
    OpenAI --> OpenAITools[Four fixed Responses function handlers]

    StrandsTools --> CoreNode[Sanitized CoreNode interface]
    OpenAITools --> CoreNode
    Health --> CoreNode
    CoreNode --> Adapter[CoreRpcNodeAdapter privacy projection]
    Adapter --> Transport[JSON-RPC HTTP transport]
    Transport --> Node[One Core-compatible node]

    Node -- raw RPC observations --> Transport
    Transport -- raw results --> Adapter
    Adapter -- sanitized peer/network health plus read-only chain data --> CoreNode

    GUI --> CredMgr[Windows Credential Manager]
    Bedrock -. existing AWS profile/session .-> AWS[AWS credential chain]

    classDef boundary fill:#eef6ff,stroke:#2764a5,stroke-width:2px;
    class Adapter,CoreNode boundary;
```

## Supervisory policy

Monitoring is off until the operator starts it. A cycle calls the same four
read-only node methods used by diagnosis and produces a normalized snapshot:

- `healthy`: no AI call;
- new or materially changed `degraded`: one diagnosis through the explicitly
  selected provider;
- unchanged `degraded`: no repeated diagnosis;
- `unavailable`: record locally without invoking a provider;
- return to `healthy`: record recovery without a recovery AI call.

Fingerprints describe the condition rather than absolute block height, so routine
chain advancement does not consume AI usage. Cycle and diagnosis locks prevent
overlap. History and deduplication are bounded, in-memory, and reset on restart.

## Privacy boundary

The JSON-RPC transport can receive raw `getnetworkinfo` and `getpeerinfo` results.
`CoreRpcNodeAdapter` projects those results onto explicit health-only fields before
returning them through `CoreNode`. Peer addresses, local/bound addresses, hostnames,
client subversions, peer/session IDs, AS mappings, proxy/listener endpoints, and
unknown peer fields are discarded at this boundary.

Providers receive the adapter, never the transport. Monitoring uses the same
adapter. Diagnostic evidence recording wraps the sanitized interface, so it does
not create a second raw-data path.

## Capability boundary

The transport allow-list is exactly:

```text
getblockchaininfo
getnetworkinfo
getpeerinfo
getchaintips
```

There is no generic RPC, wallet, transaction, filesystem, shell, restart, or
remediation capability exposed to either provider.

## Credential boundaries

- OpenAI keys are loaded from Windows Credential Manager in the GUI or from the
  `OPENAI_API_KEY` environment fallback.
- Bedrock uses the operator's existing boto3/AWS profile or session chain.
- RPC username/password or cookie contents are held in memory and are not saved
  by the desktop app.
- The synthetic harness has separate, deliberately public test credentials.
