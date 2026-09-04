# Contributing to CoreWarden

CoreWarden is a local-first, read-only monitor and AI-assisted diagnostic tool
for Bitcoin Core-compatible nodes. Bitcoin II is the initial/reference node
implementation and validated target. Contributions for other compatible nodes
are welcome; compatibility must be demonstrated rather than assumed.

## Local development

Use Python 3.10 or newer (CI uses Python 3.14 on Windows). From your checkout:

```sh
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` in a POSIX shell, then run:

```sh
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python scripts/synthetic_rpc_harness.py acceptance
```

Tests and synthetic acceptance use fake providers and fixtures; they require no
real node, API key, or paid provider call. Windows is the supported desktop/package
target. See [README.md](README.md) for configuration and optional packaging tools.

## Proposing another node

Open a **Node support** issue before a substantial integration. Include the
upstream repository, RPC documentation, default RPC port, executable names,
known RPC differences, and a reproducible test/regtest plan. Say whether you
intend to implement it. The current adapter uses four conventional Core RPC
methods; an issue should establish whether those responses are compatible or
need a small adapter change. Do not assume chain-specific timing or policy is
already supported. Node selection and chain-specific defaults remain follow-up
work, not an existing plugin system.

## Tests and security

Never commit real RPC credentials, API keys, wallet secrets, private keys, seed
phrases, `.env` files, or real provider credentials. Use explicit placeholders
in examples and fake values in tests. Sanitize screenshots, evidence, paths, and
logs before sharing. Existing adversarial secret-like fixtures test redaction;
preserve their purpose and clearly label new ones as synthetic.

Node integrations should prefer read-only diagnostic RPC methods and preserve
the current four-method allow-list and privacy filtering. Changes to that
boundary need explicit discussion. Node/RPC evidence is authoritative; AI
interprets evidence and must not control the node or perform remediation.

Add or update deterministic tests and sanitized fixtures for changed behavior,
including malformed/error responses and privacy boundaries. Avoid live-service
dependencies in ordinary tests. Report security concerns using
[SECURITY.md](SECURITY.md), without posting secret values in issues or PRs.

## Pull requests

Create a focused branch in your fork or authorized checkout. Explain the problem,
change, and validation in the PR; link the relevant issue and update affected
docs. Run the commands above and `git diff --check` before submission. Keep
unrelated refactors and generated build/release output out of the diff.
