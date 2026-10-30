"""Executa um blob de ps4_mcp/helpers no próprio PC (x86-64, mesma ABI System V do PS4).

Serve para testar o código de máquina de verdade, sem console: o blob vai para uma página rwx do
processo de teste e é chamado por ctypes. `in_fake` liga o blob a um FakeProcess, copiando a memória
do processo falso para o PC e traduzindo os endereços de volta.
"""

import ctypes
import mmap
import platform
import struct
from pathlib import Path

import pytest

HELPERS = Path(__file__).resolve().parents[1] / "ps4_mcp" / "helpers"
U64 = ctypes.c_uint64

needs_x86_64 = pytest.mark.skipif(platform.machine() not in ("x86_64", "AMD64") or platform.system() != "Linux",
                                  reason="o blob é x86-64 System V: só roda nativo em Linux x86-64")


class Blob:
    def __init__(self, name: str):
        code = (HELPERS / f"{name}.bin").read_bytes()
        self.code = code
        self._page = mmap.mmap(-1, len(code), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
        self._page.write(code)
        addr = ctypes.addressof(ctypes.c_char.from_buffer(self._page))
        self.fn = ctypes.CFUNCTYPE(U64, U64, U64, U64, U64, U64, U64)(addr)

    def __call__(self, *args: int) -> int:
        return self.fn(*args)

    def in_fake(self, proc):
        """Handler para FakeProcess.on_call_code: mesmos argumentos, memória do processo falso."""
        def run(start, length, value, size, out, cap):
            mem = (ctypes.c_char * max(length, 1)).from_buffer(bytearray(proc.read(start, length)))
            found = (U64 * max(cap, 1))()
            base = ctypes.addressof(mem)
            n = self(base, length, value, size, ctypes.addressof(found), cap)
            addrs = [found[i] - base + start for i in range(min(n, cap))]
            if addrs:
                proc.write(out, struct.pack(f"<{len(addrs)}Q", *addrs))
            return n

        return run
