# Connecting CoreWarden to Bitcoin II on Windows

CoreWarden connects to Bitcoin II through the node's local JSON-RPC interface. These are **node RPC credentials**, not wallet credentials.

CoreWarden will never ask for a seed phrase, private key, wallet passphrase, or exchange login.

## Default Bitcoin II connection

For the current Windows Bitcoin II reference setup used to validate CoreWarden:

```text
RPC URL: http://127.0.0.1:8337
Configuration file: %LOCALAPPDATA%\BitcoinII\bitcoinII.conf
```

Keep RPC bound to the local machine unless you already understand how to secure remote RPC access. Do not expose the RPC port directly to the public internet.

## Option 1: use RPC username and password

If Bitcoin II already has RPC authentication configured, use those credentials in CoreWarden.

If it does not, you can add dedicated RPC credentials to `bitcoinII.conf`.

Simple configuration:

```text
rpcuser=corewarden
rpcpassword=REPLACE_WITH_A_LONG_RANDOM_PASSWORD
```

Save the file and restart Bitcoin II, then enter the same values in CoreWarden:

```text
RPC URL: http://127.0.0.1:8337
RPC username: corewarden
RPC password: the password you created
```

`rpcuser` / `rpcpassword` is the easiest manual setup for a local node. If your Bitcoin II build supports `rpcauth`, that is preferable for a long-lived configuration because the stored config value is a salted hash rather than the plaintext RPC password.

Do not copy another person's `rpcauth` line or password. Generate your own credentials for your own node.

## Option 2: use the node's RPC cookie

CoreWarden can also authenticate with a Bitcoin Core-style RPC cookie file.

If your Bitcoin II instance creates a cookie, select the cookie-file option in CoreWarden and browse to that file. Cookie location and availability can vary by build and data-directory configuration, so CoreWarden does not assume that a cookie exists or guess its path.

If you do not see a cookie file, use explicit RPC credentials instead.

## Test the connection

With Bitcoin II running:

1. Open CoreWarden.
2. Leave the default Bitcoin II RPC URL as `http://127.0.0.1:8337`, unless your node uses a different endpoint.
3. Enter either RPC username/password or choose the node's cookie file.
4. Select **Test Node**.
5. If the node test succeeds, configure OpenAI or Bedrock separately and then use **Run Diagnosis**.

**Test Node does not invoke AI.** It performs a local read-only node check.

## What CoreWarden is allowed to read

CoreWarden's RPC boundary is fixed to these four read-only methods:

```text
getblockchaininfo
getnetworkinfo
getpeerinfo
getchaintips
```

It has no generic RPC command interface and does not access wallets, send transactions, restart the node, change configuration, or perform automatic remediation.

Where supported, a dedicated RPC account restricted to only these methods provides additional defense in depth.

## Troubleshooting

If **Test Node** fails:

- confirm Bitcoin II is running;
- confirm the RPC URL and port are correct;
- restart Bitcoin II after changing `bitcoinII.conf`;
- make sure the username and password match exactly;
- make sure you are using node RPC credentials, not wallet credentials;
- if using a cookie, confirm the selected file belongs to the currently running Bitcoin II instance;
- avoid placing credentials directly in the RPC URL.

The Bitcoin II defaults above are for CoreWarden's validated reference target. Other Core-derived nodes may use different data directories, ports, authentication settings, or RPC behavior and should not be assumed compatible until validated.
