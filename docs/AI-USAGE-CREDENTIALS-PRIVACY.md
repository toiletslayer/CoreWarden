# AI usage, credentials, and privacy

CoreWarden is **bring-your-own-credential software**. The Windows bundle and source repository do **not** contain a shared OpenAI API key, AWS credential, or maintainer-owned model access.

## What a downloaded Windows build can see

A downloaded copy runs as the current Windows user and can use credentials that are already available to that user or process.

For OpenAI, the desktop app checks for a CoreWarden credential saved in the current user's Windows Credential Manager and also supports `OPENAI_API_KEY` as a power-user environment fallback. That means a freshly downloaded copy on a machine that already has `OPENAI_API_KEY` set may show that an OpenAI credential is available. This does **not** mean the credential shipped inside the download.

Other users must supply their own OpenAI project key or configure their own AWS/Bedrock access.

## When CoreWarden uses AI

CoreWarden keeps ordinary node observation local whenever possible.

| Action | Node RPC | AI/provider call |
| --- | --- | --- |
| Open the app / view History | No | No |
| **Test Node** | Yes — one allowed read-only node call | No |
| **Test Provider — OpenAI** | No | Yes — one small tool-free `gpt-5.6-luna` Responses API request with response storage disabled |
| **Test Provider — Bedrock** | No node call | Validates AWS identity/runtime setup; actual model permission is confirmed when a diagnosis runs |
| **Run Diagnosis** | Yes — the four fixed read-only RPC observations | Yes — sanitized evidence is interpreted by the explicitly selected provider |
| Monitoring: healthy steady state | Yes — local read-only checks | No |
| Monitoring: RPC unavailable | Local check fails/records unavailable | No |
| Monitoring: recovery | Yes — local read-only checks | No recovery model call |
| Monitoring: new or materially changed degradation | Yes — local read-only checks | Yes — one diagnostic investigation for that degradation fingerprint |
| Monitoring: unchanged degradation | Yes — local read-only checks | No repeated AI call for the same condition during that monitoring session |

Provider selection is explicit. CoreWarden does not automatically fall back from OpenAI to Bedrock or from Bedrock to OpenAI.

## OpenAI model behavior

The current OpenAI provider uses `gpt-5.6-luna` through the Responses API. CoreWarden uses the model as an interpreter of already collected node evidence; it does not give the model arbitrary RPC, shell, filesystem, wallet, transaction, restart, or remediation access.

OpenAI responses are requested with response storage disabled. API use is charged, if applicable, to the user's own OpenAI project/account according to that provider's terms and pricing. CoreWarden does not provide free or shared maintainer-funded inference.

## What leaves the machine

Before any model provider can receive node observations, CoreWarden projects raw RPC results onto an explicit health-oriented field allow-list. Peer-identifying and endpoint data is filtered locally before model access, logs, or diagnostic evidence recording.

CoreWarden's node RPC allow-list is fixed to:

- `getblockchaininfo`
- `getnetworkinfo`
- `getpeerinfo`
- `getchaintips`

The AI provider receives only the sanitized diagnostic evidence needed for the investigation. The model never receives RPC usernames, RPC passwords, cookie contents, private keys, wallet secrets, or authorization headers from CoreWarden.

## Credential storage

### OpenAI

- The Windows desktop app can save the key as a generic Windows credential named `CoreWarden/OpenAI` for the current Windows user.
- The key entry field is cleared after saving and the full value is not displayed again.
- `OPENAI_API_KEY` can also be supplied through the process environment as a power-user fallback.
- The packaged download contains no OpenAI API key.
- OpenAI keys are not intentionally written to CoreWarden settings, monitoring history, logs, or diagnostic evidence.

### AWS / Bedrock

CoreWarden uses the normal AWS CLI/boto3 profile and session credential chain. It does not save AWS access keys or session tokens itself. Users authenticate their own AWS account/profile and select Bedrock explicitly.

### Node RPC credentials

RPC username/password and cookie contents are held for the running process and are not intentionally persisted by CoreWarden. Credentials embedded directly in the RPC URL are rejected.

## Monitoring and local history

Monitoring is off until the user starts it. The local monitor performs deterministic checks on the selected interval and only escalates to AI for a new or materially changed degraded condition. Healthy steady state, ordinary unavailability, recovery, and unchanged repeated degradation do not create repeated AI calls.

Sanitized monitoring history stays on the local machine. CoreWarden has no telemetry or cloud history service.

## Cost expectations

CoreWarden itself does not charge a subscription or bundle provider credits. Any OpenAI or AWS model usage belongs to the user's own provider account. The monitoring design intentionally avoids unnecessary inference by keeping healthy checks local and deduplicating unchanged degraded conditions.

For the broader safety boundary, architecture, and configuration details, see the main [README](../README.md), [ARCHITECTURE.md](../ARCHITECTURE.md), and [SECURITY.md](../SECURITY.md).
