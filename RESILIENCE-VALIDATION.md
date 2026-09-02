# CoreWarden resilience validation

Date: 2026-09-02
Baseline: `1ad97a6470f382f5c7bc0ea3c58a0a896fece818`

This is a bounded failure-injection pass, not a claim of exhaustive fault coverage. Automated
tests used loopback-only synthetic HTTP, temporary files, and fake providers. No live model
provider or real node was contacted.

## Automated synthetic results

| Scenario | Expected provider calls | Observed | Recovery | Result |
|---|---:|---:|---|---|
| Healthy → connection refused → healthy | 0 | 0 | Recorded | Passed |
| Healthy → wrong credentials / HTTP 401 → healthy | 0 | 0 | Recorded | Passed |
| Healthy → server-side authorization failure → healthy | 0 | 0 | Recorded | Passed |
| Healthy → peer-connectivity degradation, repeated | 1 | 1 | Recorded | Passed |
| Same peer fault shortly after recovery | 0 additional | 0 additional | Recorded | Passed |
| Same peer fault after 30-minute cooldown | 1 additional | 1 additional | Recorded | Passed |
| Peer degradation → header-gap degradation | 1 additional | 1 additional | Recorded | Passed |
| Header-gap degradation, repeated | 1 | 1 | Recorded | Passed |
| Warning degradation, repeated | 1 | 1 | Recorded | Passed |
| `getblockchaininfo` failure only | 0 | 0 | Recorded | Passed |
| Each other allowed RPC method fails alone | 1 per distinct fault | 1 per distinct fault | Recorded | Passed |
| Malformed JSON, non-object response, or missing result | 0 | 0 | Recorded | Passed |
| Invalid `getnetworkinfo` result type | 1 | 1 | Recorded | Passed |
| RPC response beyond configured timeout | 0 | 0 | Recorded | Passed |
| Provider timeout, throttling-like error, or generic error | 1 per case | 1 per case | Recorded | Passed |

Connection/authentication failures and failures of the primary blockchain observation classify as
`unavailable`. Failures of the other three observations retain the successful observations and
classify as controlled degradation with an `Incomplete ...` reason. Identical fingerprints do not
cause a second investigation. A different degradation fingerprint is eligible for one new
investigation.

Unchanged faults remain deduplicated. A materially changed fingerprint can escalate once without
requiring recovery. Recovery is recorded without an AI call and starts a fresh 30-minute monotonic
suppression boundary for the recovered degradation fingerprint. Recurrence of that same fault
inside the cooldown is recorded but not reinvestigated. Once the cooldown expires, the next changed
recurrence is treated as a new incident and may invoke the provider exactly once; subsequent
unchanged cycles are deduplicated again. Failed provider attempts follow the same policy.

## Provider and tool boundaries

- The RPC allow-list is exactly `getblockchaininfo`, `getnetworkinfo`, `getpeerinfo`, and
  `getchaintips`.
- The Strands surface is exactly `get_blockchain_status`, `get_network_status`,
  `get_peer_information`, and `get_chain_tips`.
- Existing OpenAI tests reject arbitrary tool names, arguments, iteration overflow, malformed
  structured output, and raw tool-exception text.
- Bedrock tests reject missing and malformed structured output and normalize construction,
  invocation, and AWS `ClientError` failures without logging raw messages. Monitoring-level fake
  provider tests separately cover timeout and throttling-like exceptions.

One defect was found: the Strands wrappers previously allowed a raw node exception to propagate to
the agent runtime. The wrappers now replace every node-tool exception with the static message:

> The fixed read-only node tool failed; treat its evidence as unavailable.

Successful tool output and the four-capability surface are unchanged. A regression test injects an
address, authorization-shaped value, and private Windows path into the exception and proves none
reach the surfaced tool error.

## Privacy and persistence

Adversarial fixtures included documentation-only addresses, hostname and subversion strings,
peer/session identifiers, AS data, listener/proxy endpoints, authorization-shaped text, an
API-key-shaped value, an AWS-account-ID-shaped value, and a private Windows path. They were absent
from provider-visible observations, persistent history, JSON export, and CSV export.

Provider failures persisted only the controlled failure category. Raw JSON-RPC error messages,
HTTP failure text, malformed response bodies, provider exception strings, and unknown source fields
were not persisted. History reload preserved order without replay or external calls. Existing and
new tests prove corrupt-file preservation and exact newest-1000 retention; JSON and CSV exports
matched the retained range.

## Concurrency and background operation

Automated tests passed for duplicate Start/Stop calls, Start/Stop/Start, overlapping-cycle
rejection, shutdown during a slow cycle, single tray ownership, repeated hide/restore, clean tray
startup failure, and lifecycle behavior that makes no provider call. No deadlock or duplicate
investigation was observed.

The packaged Windows tray flow was not rerun in this pass. Prior manual acceptance at the baseline
proved that monitoring continued while hidden through degradation, provider failure, and recovery;
restore reused the existing window; Quit stopped the application; and persisted events survived a
full restart.

## Validation summary

- Focused monitoring/resilience/provider/RPC/history/tray set: 93 passed.
- Full suite: 149 passed.
- Coverage: 91% overall; `tools.py` and `tray.py` 100%.
- No live Bedrock or OpenAI request.
- No Bitcoin II, CapStash, or Kvanta5 node contact.
- No remediation or state-changing node action.

## Remaining risks

- Live service-specific throttling and timeout behavior was represented by controlled test doubles;
  no paid provider was called.
- No destructive or failure injection was performed against a real node.
- Extended multi-hour Windows sleep/resume, network-interface churn, and abrupt process termination
  were not exercised.
- An already-running provider request retains the existing bounded shutdown semantics and is not
  forcibly terminated.
- Multi-node monitoring remains out of scope; CoreWarden monitors one configured node per app
  instance.
