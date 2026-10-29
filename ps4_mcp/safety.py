"""Segurança e auditoria (seção 8 do plano): só rede local, limites e log de toda chamada."""

from __future__ import annotations

import ipaddress
import json
import threading
import time
from pathlib import Path
from typing import Callable


class SafetyError(Exception):
    pass


def check_lan(ip: str, allow_public: bool = False) -> None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as e:
        raise SafetyError(f"PS4_IP inválido: {ip!r}. Use o IP numérico do console na rede local") from e
    if not (addr.is_private or addr.is_loopback or addr.is_link_local) and not allow_public:
        raise SafetyError(
            f"PS4_IP {ip} não é de rede local. Por segurança o ps4-mcp só fala com IPs privados "
            "(PS4_ALLOW_PUBLIC_IP=1 desliga esta trava; não exponha portas do console no roteador)"
        )


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _short(value, limit: int = 160):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…(+{len(value) - limit})"
    return value


class Audit:
    """Uma linha JSON por chamada de ferramenta em logs/audit.jsonl (lido por scripts/metrics.py)."""

    def __init__(self, path: Path, listeners: list[Callable[[dict], None]] | None = None):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.listeners = listeners or []
        self._lock = threading.Lock()

    def record(self, kind: str, name: str, args: dict | None = None, **fields) -> dict:
        entry = {"ts": round(time.time(), 3), "kind": kind, "name": name}
        if args:
            entry["args"] = {k: _short(v) for k, v in args.items()}
        entry |= {k: _short(v, 300) for k, v in fields.items()}
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        for listener in self.listeners:
            try:
                listener(entry)
            except Exception:
                pass
        return entry
