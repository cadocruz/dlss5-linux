#!/usr/bin/env python3
"""Draw the application icon, so it is reproducible rather than an opaque blob.

The look is the one AESTHETIC.md describes: near-black ground, one warm
accent, bracket markers, monospace. At 48 px a wordmark is unreadable, so the
icon keeps only what survives: the brackets and the 5.

No dependencies - a PNG is a zlib stream with a CRC per chunk, and this writes
one directly. Pixel-crisp on purpose: no anti-aliasing, which is in character
for an interface that is deliberately terminal-shaped.

    python3 packaging/make_icon.py packaging/dlss5-linux.png
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SIZE = 256
BG = (0x0B, 0x0C, 0x0E)
AMBER = (0xD8, 0xA6, 0x57)
DIM = (0x5C, 0x60, 0x69)

# A 5 on a 9x13 grid. Coarse on purpose: scaled up with hard edges it reads as
# a terminal glyph rather than as a shrunken font. The grid is this tall so the
# strokes stay two cells thick - at 7x9 they were a third of the glyph and the
# counters closed up into a blob at icon sizes.
GLYPH_5 = [
    "111111111",
    "111111111",
    "110000000",
    "110000000",
    "110000000",
    "111111110",
    "111111111",
    "000000011",
    "000000011",
    "000000011",
    "000000011",
    "111111111",
    "111111110",
]

# The brackets that mark everything in this interface. Two cells of stroke, so
# they read as brackets beside the glyph instead of as two solid bars.
BRACKET_L = [
    "111",
    "111",
    "110",
    "110",
    "110",
    "110",
    "110",
    "110",
    "110",
    "110",
    "110",
    "111",
    "111",
]
BRACKET_R = ["".join(reversed(row)) for row in BRACKET_L]


def blit(px: list[list[tuple[int, int, int]]], grid: list[str],
         x0: int, y0: int, scale: int, colour: tuple[int, int, int]) -> None:
    for gy, row in enumerate(grid):
        for gx, cell in enumerate(row):
            if cell != "1":
                continue
            for dy in range(scale):
                for dx in range(scale):
                    x, y = x0 + gx * scale + dx, y0 + gy * scale + dy
                    if 0 <= x < SIZE and 0 <= y < SIZE:
                        px[y][x] = colour


def rounded_ground(px: list[list[tuple[int, int, int]]], radius: int) -> None:
    """Square corners are the house style, but a desktop icon with hard corners
    reads as a missing icon. The radius is small enough to stay in character."""
    for y in range(SIZE):
        for x in range(SIZE):
            cx = min(x, SIZE - 1 - x)
            cy = min(y, SIZE - 1 - y)
            if cx < radius and cy < radius:
                dx, dy = radius - cx, radius - cy
                if dx * dx + dy * dy > radius * radius:
                    continue            # left transparent
            px[y][x] = BG


def png(path: Path, px: list[list[tuple[int, int, int] | None]]) -> None:
    raw = bytearray()
    for row in px:
        raw.append(0)                   # filter type 0
        for cell in row:
            if cell is None:
                raw += bytes(4)         # transparent
            else:
                raw += bytes(cell) + b"\xff"

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)   # 8-bit RGBA
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                     + chunk(b"IEND", b""))


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "packaging/dlss5-linux.png")
    px: list[list[tuple[int, int, int] | None]] = [[None] * SIZE for _ in range(SIZE)]
    rounded_ground(px, radius=40)

    scale = 13
    glyph_w, bracket_w = 9 * scale, 3 * scale
    gap = scale
    total = bracket_w + gap + glyph_w + gap + bracket_w
    x0 = (SIZE - total) // 2
    y0 = (SIZE - 13 * scale) // 2

    blit(px, BRACKET_L, x0, y0, scale, DIM)
    blit(px, GLYPH_5, x0 + bracket_w + gap, y0, scale, AMBER)
    blit(px, BRACKET_R, x0 + bracket_w + gap + glyph_w + gap, y0, scale, DIM)

    out.parent.mkdir(parents=True, exist_ok=True)
    png(out, px)
    print(f"{out} ({out.stat().st_size} bytes, {SIZE}x{SIZE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
