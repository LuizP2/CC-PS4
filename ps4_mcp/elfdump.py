"""Gera um ELF64 a partir de regiões de memória dumpadas, para abrir no Ghidra/ReVa.

O eboot.bin no disco do console é um SELF criptografado; na memória ele está decifrado e
relocado. Cada região vira um PT_LOAD no endereço real de execução, então os endereços do
Ghidra batem com rip e module_offset dos snapshots.
"""

from __future__ import annotations

import struct

EHDR = struct.Struct("<16sHHIQQQIHHHHHH")
PHDR = struct.Struct("<IIQQQQQQ")
PT_LOAD, PF_X, PF_W, PF_R = 1, 1, 2, 4
ALIGN = 0x1000


def build_elf(segments: list[tuple[int, bytes, int]], entry: int = 0) -> bytes:
    """segments: (endereço, bytes, prot com bits 1=R 2=W 4=X do mapa do ps4debug)."""
    segments = sorted(segments, key=lambda s: s[0])
    ident = b"\x7fELF" + bytes([2, 1, 1, 9]) + bytes(8)  # 64 bits, LE, versão 1, ABI FreeBSD (PS4)
    header_size = EHDR.size + PHDR.size * len(segments)
    offset = header_size
    phdrs, blobs = [], []
    for vaddr, data, prot in segments:
        offset += (vaddr - offset) % ALIGN  # p_offset ≡ p_vaddr (mod p_align)
        flags = (PF_R if prot & 1 else 0) | (PF_W if prot & 2 else 0) | (PF_X if prot & 4 else 0)
        phdrs.append(PHDR.pack(PT_LOAD, flags, offset, vaddr, vaddr, len(data), len(data), ALIGN))
        blobs.append((offset, data))
        offset += len(data)
    out = bytearray(offset)
    out[: EHDR.size] = EHDR.pack(ident, 2, 62, 1, entry, EHDR.size, 0, 0, EHDR.size, PHDR.size, len(segments), 0, 0, 0)
    for i, ph in enumerate(phdrs):
        start = EHDR.size + i * PHDR.size
        out[start : start + PHDR.size] = ph
    for pos, data in blobs:
        out[pos : pos + len(data)] = data
    return bytes(out)
