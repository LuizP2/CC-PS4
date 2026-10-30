#!/usr/bin/env python3
"""Rastreia a montagem de paks do KH3 no boot: anexa assim que o eboot.bin aparece e marca
breakpoints no visitor da varredura de pastas (arquivos encontrados) e no Mount (paks montados).

Uso: abra o jogo LOGO depois de iniciar: PS4_IP=10.42.0.145 uv run python scripts/kh3/trace_pak_mount.py [segundos]
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ps4_mcp.client import PS4Debug, PS4DebugError  # noqa: E402
from ps4_mcp.config import Config  # noqa: E402
from ps4_mcp.debug import DebugSession  # noqa: E402

VISIT = 0x6277670  # visitor de FindAllPakFiles: rsi = nome do arquivo (TCHAR*), edx = é pasta
MOUNT = 0x626def0  # FPakPlatformFile::Mount: rsi = caminho do pak, edx = ordem
FIND_ALL, FIND_ALL_ORIG = 0x626cec0, bytes.fromhex("5548")  # FindAllPakFiles: push rbp; mov rbp,rsp

cfg = Config.from_env()
client = PS4Debug(cfg.ps4_ip, cfg.debug_port)
seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 90


def wstr(pid: int, addr: int, limit: int = 260) -> str:
    raw = client.read(pid, addr, limit * 2)
    return raw.decode("utf-16le", "replace").split("\0", 1)[0]


def eboot_pids() -> set[int]:
    global client
    while True:
        try:
            return {p for p, n in client.processes() if n == "eboot.bin"}
        except (OSError, PS4DebugError):  # cabo com mau contato: reconecta e tenta de novo
            time.sleep(1)
            client = PS4Debug(cfg.ps4_ip, cfg.debug_port)


print("esperando o eboot.bin... abra o KH3 agora", flush=True)
seen = eboot_pids()
pid = None
while pid is None:
    pid = next(iter(eboot_pids() - seen), None)
    time.sleep(0.05)
s = DebugSession(cfg, client)
# o patch de depuração (CUSA12025_debug.xml) deixa um "jmp $" no início de FindAllPakFiles:
# o boot fica girando ali até anexarmos, armarmos os breakpoints e devolvermos os bytes originais
for _ in range(200):
    if client.read(pid, FIND_ALL, 2) == b"\xeb\xfe":
        break
    time.sleep(0.05)
spinning = client.read(pid, FIND_ALL, 2) == b"\xeb\xfe"
s.attach(pid)
for a, label in ((VISIT, "visit"), (MOUNT, "mount")):
    s.set_breakpoint(a, label)
if spinning:
    client.write(pid, FIND_ALL, FIND_ALL_ORIG)
print(f"anexado ao pid {pid}; breakpoints armados; jmp $ {'removido' if spinning else 'ausente (boot pode ter passado)'}", flush=True)
end = time.time() + seconds
try:
    while time.time() < end:
        ev = s.wait_event(max(0.1, end - time.time()))
        if not ev:
            break
        regs = ev.get("regs") or {}
        rip = regs.get("rip")
        if rip == VISIT:
            name = wstr(pid, regs["rsi"])
            if regs["rdx"] & 0xff or name.lower().endswith(".pak") or "kh3mod" in name.lower():
                print(f"VISIT  dir={regs['rdx'] & 0xff} {name}", flush=True)
        elif rip == MOUNT:
            print(f"MOUNT  ordem={regs['rdx'] & 0xffffffff} {wstr(pid, regs['rsi'])}", flush=True)
        else:
            print("evento", ev.get("event"), ev.get("signal"), hex(rip or 0), flush=True)
            break
        s.resume()
finally:
    print(s.detach())
