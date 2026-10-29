#!/usr/bin/env python3
"""Gera o sce_sys/icon0.png (512x512, RGB 8 bits) do CCPS4 Agent Viewer. Só stdlib.

Desenho: fundo escuro em degradê, uma "janela de terminal" com linhas coloridas
como o feed do app, e "CCPS4" / "AGENT VIEWER" escritos com a própria fonte
font8x8 do app (lida de src/core/font8x8_basic.h).

Uso: make_icon.py <font8x8_basic.h> <saida.png>
"""
from __future__ import annotations

import re
import struct
import sys
import zlib

W = H = 512


def load_font(path: str) -> list[list[int]]:
    rows = re.findall(r"\{\s*((?:0x[0-9A-Fa-f]{2}\s*,\s*){7}0x[0-9A-Fa-f]{2})\s*\}",
                      open(path, encoding="ascii").read())
    font = [[int(v, 16) for v in r.split(",")] for r in rows]
    if len(font) != 128:
        sys.exit(f"{path}: esperava 128 glifos, achei {len(font)}")
    return font


class Img:
    def __init__(self) -> None:
        self.px = [[(0, 0, 0)] * W for _ in range(H)]

    def rect(self, x: int, y: int, w: int, h: int, c) -> None:
        for yy in range(max(0, y), min(H, y + h)):
            row = self.px[yy]
            for xx in range(max(0, x), min(W, x + w)):
                row[xx] = c

    def text(self, font, s: str, x: int, y: int, scale: int, c) -> None:
        for ch in s:
            g = font[ord(ch)]
            for r in range(8):
                for b in range(8):
                    if (g[r] >> b) & 1:
                        self.rect(x + b * scale, y + r * scale, scale, scale, c)
            x += 8 * scale

    def png(self) -> bytes:
        def chunk(tag: bytes, payload: bytes) -> bytes:
            return (struct.pack(">I", len(payload)) + tag + payload
                    + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
        raw = b"".join(b"\x00" + bytes(v for p in row for v in p) for row in self.px)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    font = load_font(sys.argv[1])
    img = Img()
    for y in range(H):  # degradê vertical
        t = y / (H - 1)
        img.rect(0, y, W, 1, (int(13 + 14 * t), int(17 + 18 * t), int(23 + 26 * t)))

    # janela de terminal
    img.rect(36, 36, 440, 268, (48, 54, 61))
    img.rect(40, 40, 432, 260, (13, 17, 23))
    img.rect(40, 40, 432, 30, (22, 27, 34))
    for i, c in enumerate([(248, 81, 73), (210, 153, 34), (63, 185, 80)]):
        img.rect(56 + i * 26, 49, 14, 14, c)
    lines = [((79, 195, 247), 150), ((165, 214, 167), 250), ((255, 183, 77), 210),
             ((126, 231, 135), 190), ((179, 157, 219), 120)]
    for i, (c, w) in enumerate(lines):
        y = 90 + i * 40
        img.rect(60, y, 48, 16, (110, 118, 129))
        img.rect(122, y, 52, 16, c)
        img.rect(188, y, w, 16, (201, 209, 217) if i % 2 else c)
    img.rect(60, 278, 16, 4, (230, 237, 243))  # cursor

    img.text(font, "CCPS4", 76, 330, 9, (88, 166, 255))
    img.text(font, "CCPS4", 79, 330, 9, (88, 166, 255))
    img.text(font, "AGENT VIEWER", 64, 428, 4, (230, 237, 243))
    with open(sys.argv[2], "wb") as fh:
        fh.write(img.png())


if __name__ == "__main__":
    main()
