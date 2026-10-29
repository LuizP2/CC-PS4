#!/usr/bin/env python3
"""Fase 0 do plano: valida as primitivas sem IA.

Conecta no ps4debug, lista os processos e lê 64 bytes do jogo em execução; opcionalmente
testa o FTP e a captura de logs. "Pronto quando" a fase 0 passa: este script termina com OK.

Uso: uv run python scripts/fase0_smoke.py --ip 192.168.0.50 [--pid N] [--ftp] [--logs]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ps4_mcp.client import PS4Debug, PS4DebugError  # noqa: E402
from ps4_mcp.text import hexdump  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default=os.environ.get("PS4_IP", ""))
    ap.add_argument("--port", type=int, default=744)
    ap.add_argument("--pid", type=int, default=0, help="pid do jogo (padrão: o único eboot.bin)")
    ap.add_argument("--ftp", action="store_true", help="também lista / pelo FTP (porta 2121)")
    ap.add_argument("--logs", action="store_true", help="também escuta o klog (porta 3232) por 5 s")
    args = ap.parse_args()
    if not args.ip:
        ap.error("informe --ip ou defina PS4_IP")

    client = PS4Debug(args.ip, args.port)
    try:
        print(f"ps4debug {client.version()} em {args.ip}:{args.port}")
        procs = client.processes()
        print(f"{len(procs)} processos:")
        for pid, name in sorted(procs):
            print(f"  {pid:>6} {name}")
        pid = args.pid or next((p for p, n in procs if n == "eboot.bin"), 0)
        if not pid:
            print("nenhum eboot.bin rodando: abra um jogo/homebrew ou passe --pid")
            return 1
        print(f"info: {client.process_info(pid)}")
        maps = client.maps(pid)
        region = next((m for m in maps if m.name == "eboot.bin" and m.prot & 1), None) or next(m for m in maps if m.prot & 1)
        print(f"{len(maps)} regiões; lendo 64 bytes de {region.name} @ {region.start:#x}")
        print(hexdump(client.read(pid, region.start, 64), region.start))
    except PS4DebugError as e:
        print(f"FALHOU: {e}")
        return 1

    if args.ftp:
        from ps4_mcp.ftp import ConsoleFTP, FTPError

        try:
            entries = ConsoleFTP(args.ip).list_dir("/")
            print(f"FTP ok: {', '.join(e['name'] for e in entries[:12])}")
        except FTPError as e:
            print(f"FTP FALHOU: {e}")
            return 1
    if args.logs:
        from ps4_mcp.logs import LogReader

        reader = LogReader(f"tcp:{args.ip}:3232")
        reader.start()
        time.sleep(5)
        print(f"logs: {reader.status()}")
        print("\n".join(reader.tail(10)))
        reader.stop()
    print("OK: fase 0 concluída")
    return 0


if __name__ == "__main__":
    sys.exit(main())
