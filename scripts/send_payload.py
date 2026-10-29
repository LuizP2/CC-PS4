#!/usr/bin/env python3
"""Envia um payload .bin (ex.: ps4debug.bin) para o BinLoader do GoldHEN (porta 9090).

Uso: uv run python scripts/send_payload.py ps4debug.bin --ip 192.168.0.50 [--port 9090]
Confira se a versão do payload é a do seu firmware antes de enviar.
"""

import argparse
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ps4_mcp.config import discover_ps4  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("payload", type=Path)
    ap.add_argument("--ip", default=os.environ.get("PS4_IP", ""))
    ap.add_argument("--port", type=int, default=9090)
    args = ap.parse_args()
    if args.ip in ("", "auto"):
        args.ip = discover_ps4()
    if not args.ip:
        ap.error("PS4 não encontrado na rede: informe --ip ou defina PS4_IP")
    data = args.payload.read_bytes()
    if not data:
        ap.error(f"{args.payload} está vazio")
    try:
        with socket.create_connection((args.ip, args.port), timeout=10) as s:
            s.sendall(data)
    except OSError as e:
        print(f"falhou: {e}. O BinLoader do GoldHEN está ativo (Settings → Server → BinLoader)?")
        return 1
    print(f"enviados {len(data)} bytes para {args.ip}:{args.port}; aguarde a notificação do payload no console")
    return 0


if __name__ == "__main__":
    sys.exit(main())
