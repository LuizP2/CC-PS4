#!/usr/bin/env python3
"""Gera o icon0.png (512x512, RGB 8 bits) do CCPS4 Crash Test só com a stdlib.

Desenho: fundo escuro, faixa de "fita de perigo" (amarelo/preto) em cima e
embaixo e um octógono vermelho com um "!" branco no meio.

Uso: make_icon.py <saida.png>
"""
from __future__ import annotations

import struct
import sys
import zlib

W = H = 512


def png_bytes(width: int, height: int, rows: list[bytearray]) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(r) for r in rows)  # filtro 0 (None) em todas as linhas
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    bg, red, white = (0x1B, 0x0F, 0x12), (0xE5, 0x39, 0x35), (0xFF, 0xFF, 0xFF)
    yellow, black = (0xFF, 0xC1, 0x07), (0x11, 0x11, 0x11)
    rows = []
    cx = cy = 256
    for y in range(H):
        row = bytearray()
        for x in range(W):
            c = bg
            if y < 56 or y >= H - 56:  # fita de perigo
                c = yellow if ((x + y) // 32) % 2 == 0 else black
            dx, dy = abs(x - cx), abs(y - cy)
            if dx <= 170 and dy <= 170 and dx + dy <= 240:  # octógono
                c = red
                if dx <= 26 and -120 <= y - cy <= 50:     # haste do "!"
                    c = white
                if dx <= 28 and 82 <= y - cy <= 136:      # ponto do "!"
                    c = white
            row += bytes(c)
        rows.append(row)
    with open(sys.argv[1], "wb") as fh:
        fh.write(png_bytes(W, H, rows))


if __name__ == "__main__":
    main()
