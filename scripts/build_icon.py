"""Build CoreWarden's Windows icon from the approved native-size PNG assets."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICON_SIZES = (32, 64, 128)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise ValueError("Branding input is not a valid PNG with an IHDR header")
    return struct.unpack(">II", data[16:24])


def build_icon(assets_dir: Path, output: Path) -> None:
    """Write an ICO that embeds each approved PNG byte-for-byte at its native size."""
    images: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        path = assets_dir / f"Sprite{size}.png"
        data = path.read_bytes()
        if _png_dimensions(data) != (size, size):
            raise ValueError(f"{path.name} must be exactly {size}x{size}")
        images.append((size, data))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries: list[bytes] = []
    payloads: list[bytes] = []
    for size, data in images:
        entries.append(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(data), offset))
        payloads.append(data)
        offset += len(data)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + b"".join(entries) + b"".join(payloads))


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets-dir", type=Path, default=project_root / "assets")
    parser.add_argument("--output", type=Path, default=project_root / "assets" / "corewarden.ico")
    args = parser.parse_args()
    build_icon(args.assets_dir, args.output)


if __name__ == "__main__":
    main()
