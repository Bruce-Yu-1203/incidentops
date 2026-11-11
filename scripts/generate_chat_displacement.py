"""Build the chat lens lookup texture offline, keeping Canvas work off the click path."""

from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + kind
        + data
        + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def radial_displacement_png(size: int = 256) -> bytes:
    """Preserve the original neutral center and radial squeeze/stretch ring."""
    pixels = bytearray()
    for row in range(size):
        pixels.append(0)  # PNG scanline filter: none.
        for column in range(size):
            u = ((column + 0.5) / size) * 2 - 1
            v = ((row + 0.5) / size) * 2 - 1
            distance = math.hypot(u, v)
            force = math.sin(math.pi * (distance - 0.62) / 0.38) if 0.62 < distance < 1 else 0
            force *= 0.55 + 0.45 * min(1, max(0, (distance - 0.62) / 0.38))
            dx = u / distance if distance else 0
            dy = v / distance if distance else 0
            pixels.extend((round(128 - dx * force * 127), round(128 - dy * force * 127), 128, 255))
    header = struct.pack("!2I5B", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(pixels), 9))
        + _chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check the asset without rewriting it")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "apps/web/public/chat-displacement-v1.png"
    expected = radial_displacement_png()
    if args.check:
        if target.read_bytes() != expected:
            raise SystemExit("Chat displacement texture is out of date")
    else:
        target.write_bytes(expected)
