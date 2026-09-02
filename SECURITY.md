# Security policy

## Supported versions

CoreWarden is pre-1.0. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose node RPC
credentials, peer data, wallet material, or enable state-changing RPC calls.
Use GitHub's private **Report a vulnerability** security-advisory form when it
is available for the repository. If private vulnerability reporting is not yet
enabled, contact the repository owner and request a private channel before
sharing details. Include reproduction steps, impact, and the affected version.
Do not include live secrets in an initial report.

## Operator guidance

Use a dedicated, least-privilege RPC identity; bind node RPC to a trusted
interface; prefer a local authenticated proxy for remote access; never commit
credentials; and review model-provider data handling before submitting peer data.

OpenAI keys belong in Windows Credential Manager or `OPENAI_API_KEY`. AWS
authentication uses the operator's existing AWS configuration/session. RPC
passwords and cookie contents are session-only in the desktop app. The synthetic
harness credentials are public test fixtures and must never be replaced with
real node credentials.

Sanitized monitoring history is stored only in the current user's non-roaming
`%LOCALAPPDATA%\CoreWarden\history` directory and is capped at 1000 events. The
persisted/exported schema is allow-listed and contains controlled event reasons and
validated investigation metadata, never raw RPC/provider payloads, prompts, arbitrary
exceptions, credentials, peer identifiers, addresses, endpoints, hostnames, client
subversions, AS mappings, proxy data, wallet data, or transaction data. JSON/CSV
exports should still be treated as local operational records and shared deliberately.

CoreWarden is a diagnostic aid, not a consensus oracle or a replacement for node
monitoring and human judgment.
