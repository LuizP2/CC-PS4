#!/usr/bin/env python3
"""Mede a velocidade de leitura de memória do PS4 (ps4debug) e a do FTP, para comparar redes.

Uso: uv run python scripts/bench_read.py --ip 192.168.0.50 [--mib 16] [--conns 1]
Lê de um processo do sistema (SceShellUI), sem precisar de jogo aberto e sem attach.
Se o ps4debug e o FTP derem o mesmo número, o limite é a rede, não o payload.
"""

import argparse
import ftplib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ps4_mcp.client import PS4Debug, PS4DebugError  # noqa: E402
from ps4_mcp.config import discover_ps4  # noqa: E402

CHUNK = 1 << 20


def bench_debug(ip: str, total: int, conns: int) -> float:
    base = PS4Debug(ip, 744)
    pid = next(p for p, n in base.processes() if n == "SceShellUI")
    region = max((m for m in base.maps(pid) if m.prot & 1), key=lambda m: m.end - m.start)
    total = min(total, region.end - region.start)
    clients = [PS4Debug(ip, 744) for _ in range(conns)]
    for c in clients:
        c.version()  # abre a conexão antes de medir: o aceite do payload dorme 2 s
    offsets = range(0, total, CHUNK)
    t = time.time()
    with ThreadPoolExecutor(conns) as ex:
        list(ex.map(lambda i: clients[i % conns].read(pid, region.start + offsets[i], CHUNK), range(len(offsets))))
    return total / (time.time() - t) / 2**20


def bench_ftp(ip: str, total: int) -> float | None:
    """Baixa até `total` bytes do maior arquivo em /system/common/lib (sempre existe)."""
    f = ftplib.FTP()
    f.connect(ip, 2121, timeout=30)
    f.login()
    sizes = {}
    for name in f.nlst("/system/common/lib"):
        name = "/system/common/lib/" + name.rsplit("/", 1)[-1]  # o NLST do GoldHEN devolve só o nome
        try:
            sizes[name] = f.size(name) or 0
        except ftplib.error_perm:
            pass
    if not sizes:
        return None
    got, t = 0, time.time()
    conn = f.transfercmd("RETR " + max(sizes, key=sizes.get))
    while got < total and (data := conn.recv(CHUNK)):
        got += len(data)
    conn.close()
    return got / (time.time() - t) / 2**20


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default=os.environ.get("PS4_IP", ""))
    ap.add_argument("--mib", type=int, default=16, help="quanto ler em cada teste")
    ap.add_argument("--conns", default="1", help="conexões paralelas a testar, ex.: 1,2 (8 conexões coincidiram com um kernel panic)")
    args = ap.parse_args()
    if args.ip in ("", "auto"):
        args.ip = discover_ps4()
    if not args.ip:
        ap.error("PS4 não encontrado na rede: informe --ip ou defina PS4_IP")
    total = args.mib << 20
    try:
        for n in (int(x) for x in args.conns.split(",")):
            print(f"ps4debug, {n} conexão(ões): {bench_debug(args.ip, total, n):5.1f} MiB/s")
    except PS4DebugError as e:
        print(f"ps4debug falhou: {e}")
    try:
        speed = bench_ftp(args.ip, total)
        print(f"FTP: {speed:5.1f} MiB/s" if speed else "FTP: nenhum arquivo para baixar")
    except (OSError, EOFError) as e:
        print(f"FTP falhou: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
