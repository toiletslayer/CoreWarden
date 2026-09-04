# CoreWarden roadmap

CoreWarden v1 was created by [`toiletslayer`](https://github.com/toiletslayer) and initially developed and live-validated against Bitcoin II on Windows.

The project is intentionally broader than one chain. The goal is a Windows-first, read-only diagnostic and monitoring tool that can support additional Bitcoin Core-compatible cryptocurrency nodes through validation, small compatibility adapters where needed, and community contributions.

## Current reference target

| Project | Status | Notes |
| --- | --- | --- |
| Bitcoin II | Validated reference target | Original live-tested implementation; Windows desktop currently pre-fills its RPC port `8337`. |

## Candidate community validations

These are **not claimed as supported yet**. They are projects we would be interested in validating with help from people who actually run or maintain them.

| Project | Current status |
| --- | --- |
| Kvanta5 | Candidate for compatibility validation / adapter work |
| CapStash | Candidate for compatibility validation / adapter work |
| Litecoin | Candidate for compatibility validation / adapter work |
| Other Core-derived nodes | Contributions and test reports welcome |

A candidate should become a supported/validated target only after its four diagnostic RPC responses and relevant edge cases are tested against CoreWarden. Compatibility should be demonstrated rather than assumed.

## Useful next contributions

- Validate another Core-derived node on Windows using sanitized fixtures and/or a reproducible test setup.
- Add a small `CoreNode` implementation when a project's RPC responses differ from the current Core-style adapter.
- Design explicit node/network profiles so the desktop app does not rely on the Bitcoin II `8337` prefill.
- Improve deterministic health checks without weakening the four-method, read-only RPC boundary.
- Add privacy and malformed-response fixtures from additional Core implementations.
- Improve packaging, documentation, accessibility, and operator experience on Windows.
- Suggest other useful applications for the sanitized, read-only evidence layer.

## Have a different idea?

CoreWarden is early enough to shape. If you run a Core-based cryptocurrency on Windows, maintain one, operate several nodes, or see another useful application for the evidence layer, open an issue and describe the problem you would like CoreWarden to help with.

The project deliberately does **not** give AI control of the node. Node/RPC evidence remains authoritative, and state-changing RPC, wallets, transactions, restarts, arbitrary shell access, and automatic remediation stay outside the current safety boundary.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, testing, security expectations, and the node-support contribution path.
