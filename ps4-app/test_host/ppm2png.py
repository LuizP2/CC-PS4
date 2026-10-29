#!/usr/bin/env python3
"""Converte PPM binário (P6, 8 bits) em PNG RGB usando só a stdlib (zlib).

Uso: ppm2png.py entrada.ppm saida.png
"""
from __future__ import annotations

import struct
import sys
import zlib


def read_ppm(path: str) -> tuple[int, int, bytes]:
    data = open(path, "rb").read()
    tokens: list[bytes] = []
    i = 0
    while len(tokens) < 4:
        while data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while data[i:i + 1] not in (b"\n", b""):
                i += 1
            continue
        j = i
        while not data[j:j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    i += 1  # um único espaço separa o cabeçalho dos pixels
    if tokens[0] != b"P6" or int(tokens[3]) != 255:
        sys.exit(f"{path}: só PPM P6 com maxval 255")
    w, h = int(tokens[1]), int(tokens[2])
    pixels = data[i:i + w * h * 3]
    if len(pixels) != w * h * 3:
        sys.exit(f"{path}: PPM truncado")
    return w, h, pixels


def write_png(path: str, w: int, h: int, rgb: bytes) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    stride = w * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(h))
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(chunk(b"IEND", b""))


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    w, h, rgb = read_ppm(sys.argv[1])
    write_png(sys.argv[2], w, h, rgb)
    print(f"{sys.argv[2]}: {w}x{h}")


if __name__ == "__main__":
    main()
