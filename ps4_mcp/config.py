"""Configuração do ps4-mcp, lida de variáveis de ambiente (definidas no .mcp.json)."""

from __future__ import annotations

import os
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw, 0) if raw else default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in ("1", "true", "yes", "sim", "on") if raw else default


DDP_PORT = 987  # Device Discovery Protocol, o mesmo do Remote Play
DDP_SEARCH = b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00020020\n"


def discover_ps4(debug_port: int = 744, timeout: float = 1.5, ddp_addr=("255.255.255.255", DDP_PORT)) -> str:
    """Acha o PS4 na rede local. Tenta o DDP em broadcast e, sem resposta, varre a /24 do PC
    atrás do ps4debug. Devolve "" se não achar."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        try:
            s.sendto(DDP_SEARCH, ddp_addr)
            while True:
                data, (ip, _) = s.recvfrom(2048)
                if b"host-type:PS4" in data:
                    return ip
        except OSError:
            pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 9))  # só escolhe a interface da rota padrão, nada é enviado
            pc_ip = s.getsockname()[0]
    except OSError:
        return ""
    # shortcut: assume máscara /24, que é a de quase todo roteador doméstico; use PS4_IP em outras redes
    prefix = pc_ip.rsplit(".", 1)[0]
    hosts = [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != pc_ip]

    def probe(ip: str) -> str:
        try:
            socket.create_connection((ip, debug_port), timeout=0.5).close()
            return ip
        except OSError:
            return ""

    with ThreadPoolExecutor(64) as pool:
        return next((ip for ip in pool.map(probe, hosts) if ip), "")


@dataclass
class Config:
    ps4_ip: str = ""
    debug_port: int = 744  # porta de comandos do ps4debug
    callback_port: int = 755  # porta em que o PC escuta as interrupções (fixa no payload)
    callback_bind: str = "0.0.0.0"
    ftp_port: int = 2121
    ftp_user: str = "anonymous"
    ftp_pass: str = ""
    log_source: str = ""  # tcp:IP:PORTA | udp:BIND:PORTA | file:/caminho | off
    max_read: int = 64 * 1024
    inline_hex_max: int = 1024  # leituras maiores viram arquivo + resumo
    max_breakpoints: int = 10
    max_wait_s: int = 300
    max_dump_mb: int = 512
    max_download_mb: int = 4096
    allow_public_ip: bool = False
    enable_write: bool = False
    write_confirm: str = "elicit"  # elicit | none
    enable_deploy: bool = False
    workdir: Path = field(default_factory=Path.cwd)
    bridge: bool = False
    bridge_port: int = 8765
    bridge_discovery_port: int = 8766
    rpi_port: int = 12800
    toolchain: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        ip = os.environ.get("PS4_IP", "").strip()
        if ip in ("", "auto"):
            ip = discover_ps4(_int("PS4DEBUG_PORT", 744))
        workdir = Path(os.environ.get("PS4_WORKDIR", "") or Path.cwd()).expanduser()
        log_default = f"tcp:{ip}:3232" if ip else "off"
        return cls(
            ps4_ip=ip,
            debug_port=_int("PS4DEBUG_PORT", 744),
            callback_port=_int("PS4DEBUG_CALLBACK_PORT", 755),
            callback_bind=os.environ.get("PS4DEBUG_CALLBACK_BIND", "0.0.0.0"),
            ftp_port=_int("PS4_FTP_PORT", 2121),
            ftp_user=os.environ.get("PS4_FTP_USER", "anonymous"),
            ftp_pass=os.environ.get("PS4_FTP_PASS", ""),
            log_source=os.environ.get("PS4_LOG_SOURCE", "").strip() or log_default,
            max_read=_int("PS4_MAX_READ", 64 * 1024),
            inline_hex_max=_int("PS4_INLINE_HEX_MAX", 1024),
            max_breakpoints=min(_int("PS4_MAX_BREAKPOINTS", 10), 30),
            max_wait_s=_int("PS4_MAX_WAIT_S", 300),
            max_dump_mb=_int("PS4_MAX_DUMP_MB", 512),
            max_download_mb=_int("PS4_MAX_DOWNLOAD_MB", 4096),
            allow_public_ip=_bool("PS4_ALLOW_PUBLIC_IP"),
            enable_write=_bool("PS4_ENABLE_WRITE"),
            write_confirm=os.environ.get("PS4_WRITE_CONFIRM", "elicit").strip().lower(),
            enable_deploy=_bool("PS4_ENABLE_DEPLOY"),
            workdir=workdir,
            bridge=_bool("PS4_BRIDGE"),
            bridge_port=_int("PS4_BRIDGE_PORT", 8765),
            bridge_discovery_port=_int("PS4_BRIDGE_DISCOVERY_PORT", 8766),
            rpi_port=_int("PS4_RPI_PORT", 12800),
            toolchain=os.environ.get("OO_PS4_TOOLCHAIN", ""),
        )

    def out_dir(self, name: str) -> Path:
        path = self.workdir / name
        path.mkdir(parents=True, exist_ok=True)
        return path
