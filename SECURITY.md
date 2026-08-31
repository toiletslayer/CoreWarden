# Security policy

## Supported versions

CoreWarden is pre-1.0. Security fixes are applied to the latest release.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose node RPC
credentials, peer data, wallet material, or enable state-changing RPC calls.
Contact the repository maintainers privately and include reproduction steps,
impact, and the affected version. Do not include live secrets.

## Operator guidance

Use a dedicated, least-privilege RPC identity; bind node RPC to a trusted
interface; prefer a local authenticated proxy for remote access; never commit
credentials; and review model-provider data handling before submitting peer data.

CoreWarden is a diagnostic aid, not a consensus oracle or a replacement for node
monitoring and human judgment.
