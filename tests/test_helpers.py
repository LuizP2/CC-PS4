"""O código de máquina de ps4_mcp/helpers, executado de verdade no PC."""

import ctypes
import struct

from tests.native_blob import U64, Blob, needs_x86_64

pytestmark = needs_x86_64


def _scan(blob, data: bytes, value: int, size: int, cap: int = 64):
    buf = (ctypes.c_char * len(data)).from_buffer(bytearray(data))
    out = (U64 * cap)()
    base = ctypes.addressof(buf)
    n = blob(base, len(data), value, size, ctypes.addressof(out), cap)
    return n, [out[i] - base for i in range(min(n, cap))]


def test_scan_value_all_sizes_aligned_only():
    blob = Blob("scan_value")
    data = bytearray(4096)
    struct.pack_into("<I", data, 0x100, 37)
    struct.pack_into("<I", data, 0x205, 37)          # desalinhado: fica de fora
    struct.pack_into("<I", data, 0xFFC, 37)          # última posição do buffer
    struct.pack_into("<q", data, 0x400, -5)
    struct.pack_into("<H", data, 0x502, 0xBEEF)
    data[0x601] = 0x7F
    assert _scan(blob, data, 37, 4) == (2, [0x100, 0xFFC])
    assert _scan(blob, data, -5 & (1 << 64) - 1, 8) == (1, [0x400])
    assert _scan(blob, data, 0xBEEF, 2) == (1, [0x502])
    assert _scan(blob, data, 0x7F, 1) == (1, [0x601])
    assert _scan(blob, data, 37 | 0xAB00000000, 4) == (2, [0x100, 0xFFC])  # só os 4 bytes baixos contam
    assert _scan(blob, data, 37, 3) == (0, [])         # tamanho inválido


def test_scan_value_cap_and_partial_tail():
    blob = Blob("scan_value")
    n, found = _scan(blob, bytes(64), 0, 4, cap=5)
    assert n == 16 and found == [0, 4, 8, 12, 16]      # total real, lista cortada em cap
    assert _scan(blob, bytes(7), 0, 4)[0] == 1          # o resto que não fecha um u32 é ignorado
