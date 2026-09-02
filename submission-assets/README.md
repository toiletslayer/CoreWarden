# CoreWarden submission assets

These files are presentation assets for the AWS Agents for Humans Devpost entry, the public GitHub README, and demo-video overlays. They do not change CoreWarden runtime behavior.

| File | Dimensions | What it demonstrates | Data source | Live provider call for capture | Recommended use |
| --- | ---: | --- | --- | --- | --- |
| `corewarden-architecture.svg` | 1920 × 1080 | Local-first monitoring, meaningful-change escalation, the provider-neutral diagnosis workflow, the primary Amazon Bedrock / Strands path, secondary OpenAI support, the privacy projection/filter boundary, and the exact four read-only RPC capabilities. | Derived from `ARCHITECTURE.md` and the current implementation; no node data. | No | Devpost architecture section, GitHub README, lossless demo-video overlays |
| `corewarden-architecture.png` | 1920 × 1080 | Raster presentation version of the architecture diagram. | Same as the SVG. | No | Devpost image gallery, slide/video overlays, social previews |
| `corewarden-healthy.png` | 1042 × 1032 | Native CoreWarden GUI with Amazon Bedrock selected, loopback node shown connected, deterministic monitoring Healthy, and no AI investigation. | Synthetic state replayed through the real native GUI presentation layer; no real node. | No | Devpost product screenshot, GitHub README, demo-video healthy-state segment |
| `corewarden-degraded-strands.png` | 1042 × 1032 | Native CoreWarden GUI showing the validated degraded peer-connectivity condition, Amazon Bedrock / Strands investigation, `suspicious` classification, and 82% confidence. | Replayed result from the previously validated synthetic monitoring acceptance run; no new node or model execution. | No new call; the displayed result records the earlier validated acceptance result. | Primary Devpost product screenshot, GitHub README, demo-video investigation segment |
| `corewarden-recovery.png` | 1042 × 1032 | Native CoreWarden GUI showing Healthy → Degraded → AI investigation → Node recovered history, while preserving the last investigation and making clear that recovery caused no provider call. | Synthetic/replayed state through the real native GUI presentation layer; no real node. | No | Devpost supporting screenshot, demo-video recovery segment |
| `corewarden-logo.png` | 128 × 128 | Approved CoreWarden character artwork. | Exact copy of `assets/Sprite128.png`. | No | Devpost logo/avatar, GitHub presentation, video title cards |

## Capture and rendering notes

- The architecture SVG was authored directly from the repository architecture and implementation, without a browser renderer. The PNG was rasterized from that SVG with an isolated, development-only CairoSVG installation outside the repository. No CoreWarden dependency declaration was changed.
- GUI screenshots are native Windows/Tk captures of the existing `CoreWardenDesktop` presentation layer. A temporary replay-only launcher populated existing GUI fields and status widgets with synthetic or previously validated values; it did not call monitoring, RPC, Bedrock, or OpenAI code and is not included in this folder.
- All credential inputs are blank. The only endpoint visible is the synthetic loopback address `127.0.0.1:18443`. No API keys, passwords, cookie contents, AWS account identifiers, authorization values, peer addresses/identifiers, raw RPC output, private filesystem paths, or unrelated desktop content are present.
- No live Bedrock or OpenAI request and no real Bitcoin II node contact occurred while producing these assets.
