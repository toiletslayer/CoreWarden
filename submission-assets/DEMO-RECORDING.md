# CoreWarden manual demo recording plan

Automated native-window MP4 capture was not reliable on this host: FFmpeg/gdigrab and OBS are not installed, and Xbox Game Bar is intentionally interactive. Do not add recording code or a CoreWarden runtime dependency. Record with **Xbox Game Bar** (`Win+Alt+R`) or OBS on a clean desktop.

## Safe setup

Use the packaged executable:

From the repository root, launch the exact packaged executable:

```text
dist\CoreWarden\CoreWarden.exe
```

For an optional live synthetic-node segment, start the loopback-only harness from the repository:

```powershell
python scripts\synthetic_rpc_harness.py serve
```

Its endpoint is `http://127.0.0.1:18443`. Enter the documented test-only authentication while recording is paused, then leave the password field masked. Never use a real node credential. The scenario file is changed with:

```powershell
$scenarioFile = Join-Path ([IO.Path]::GetTempPath()) 'corewarden-synthetic-scenario.txt'
Set-Content -LiteralPath $scenarioFile -Value 'healthy'
Set-Content -LiteralPath $scenarioFile -Value 'degraded_peer_connectivity'
Set-Content -LiteralPath $scenarioFile -Value 'recovered'
```

Production GUI monitoring keeps its five-minute minimum. For a three-to-four-minute submission cut, record the current native GUI and use the validated replay screenshots below for the transition/result/history shots. Add a visible caption such as **“Validated Strands acceptance result — replayed; no new model request.”** Do not imply that the replay is a fresh provider invocation.

## Window preparation

1. Use a clean Windows virtual desktop with notifications disabled.
2. Close or hide Codex, terminals, wallets, node GUIs, Discord, and personal applications.
3. Set display scaling to the value used for the screenshots and center CoreWarden at roughly 1000 × 1000 pixels.
4. Select Amazon Bedrock. Keep AWS profile blank, region `us-west-2`, advanced model setting collapsed, RPC username/password/cookie fields out of focus, and all secrets absent.
5. Record only the CoreWarden window. Do not record the taskbar unless the tray segment has been explicitly checked for unrelated icons or notifications.

## Timed shot list (about 2:45)

| Time | Action | Expected visible result |
| ---: | --- | --- |
| 0:00–0:12 | Title card using `corewarden-logo.png` and product name. | CoreWarden branding; no private desktop content. |
| 0:12–0:35 | Show `corewarden-healthy.png` or the equivalent live synthetic healthy GUI. | Node Connected, Monitoring Healthy, Last AI investigation Never; explain that healthy checks stay local. |
| 0:35–0:55 | Brief architecture crop/full frame from `corewarden-architecture.png`. | Local deterministic monitoring, privacy boundary, and the primary Bedrock/Strands route are readable. |
| 0:55–1:25 | Show `corewarden-degraded-strands.png` with the replay caption. | Degraded peer connectivity; Amazon Bedrock / Strands; suspicious; 82%; no fresh provider call. |
| 1:25–1:40 | Hold the same degraded frame and call out recurrence/deduplication. | Unchanged fault does not create an immediate second investigation. |
| 1:40–2:00 | Show `corewarden-recovery.png`. | Monitoring Healthy again; last validated investigation retained; no recovery AI call. |
| 2:00–2:30 | Show `corewarden-history.png`. | Local-offset timestamps and the sanitized healthy/degraded/investigation/recovery sequence; JSON/CSV controls visible. |
| 2:30–2:45 | Return to the architecture or logo. | End on local-first, read-only, privacy-filtered positioning. |

## Tray segment

The tray shot is optional. Capture it only on a clean virtual desktop after confirming that no unrelated icons, notifications, account names, or private windows are visible. Start monitoring, close the CoreWarden window, open only the CoreWarden tray menu, show `Monitoring: Active`, then choose `Open CoreWarden`. If the taskbar cannot be isolated cleanly, omit this shot.

## Final review

- Verify the MP4 is H.264/AAC (or H.264 without audio), 1920 × 1080, and no longer than four minutes.
- Watch the full export at normal speed and inspect paused frames around every window switch.
- Confirm there are no keys, tokens, account IDs, RPC credentials, peer addresses, hostnames, private paths, terminals, or unrelated desktop content.
- Confirm every replayed diagnosis is labeled as replayed and previously validated.
