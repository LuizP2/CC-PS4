"""Cliente das primitivas do ps4debug: processos, memória, kernel e notificações.

Regras do payload que este cliente respeita (ver docs/protocolo-ps4debug.md):
- todo status de erro faz o payload fechar a conexão (server.c: handler retorna 1 → free_client);
- PROC_READ manda SUCCESS antes de ler e devolve zeros em memória não mapeada (proc.c);
- só operações idempotentes são repetidas automaticamente depois de queda de conexão.
"""

from __future__ import annotations

import socket
import threading
import time

from .protocol import (
    HEADER, KERN_READ, MAP_ENTRY, NOTIFY, PACKET_MAGIC, PID, PROC_ALLOC, PROC_CALL, PROC_CALL_RESP,
    PROC_FREE, PROC_INFO, PROC_LIST_ENTRY, PROC_RW, STATUS_TEXT, U32, U64, Cmd, MapEntry, Status, cstr,
)

MAPS_TTL_S = 5.0


class PS4DebugError(Exception):
    """Falha de comunicação ou de uso do ps4debug (mensagem pronta para o agente)."""


class StatusError(PS4DebugError):
    def __init__(self, cmd: int, status: int):
        self.cmd, self.status = cmd, status
        name = Cmd(cmd).name if cmd in Cmd._value2member_map_ else hex(cmd)
        text = STATUS_TEXT.get(status, "status desconhecido")
        super().__init__(f"ps4debug respondeu {status:#010x} ({text}) a {name}")


class Connection:
    """Uma conexão TCP com o ps4debug (porta 744)."""

    def __init__(self, host: str, port: int, timeout: float = 10.0):
        if not host:
            raise PS4DebugError("PS4_IP não configurado: defina PS4_IP no .mcp.json ou no ambiente")
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise PS4DebugError(
                f"não conectei no ps4debug em {host}:{port} ({e}). O payload está carregado? "
                "(GoldHEN → enviar ps4debug.bin pelo BinLoader, porta 9090)"
            ) from e
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.alive = True

    def send(self, cmd: int, payload: bytes = b"") -> None:
        # Cabeçalho e dados num único envio: o payload lê o cabeçalho com recv sem "force"
        # (server.c), então um cabeçalho fragmentado seria descartado.
        self.sock.sendall(HEADER.pack(PACKET_MAGIC, cmd, len(payload)) + payload)

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def recv(self, size: int) -> bytes:
        buf = bytearray(size)
        view, got = memoryview(buf), 0
        while got < size:
            n = self.sock.recv_into(view[got:])
            if n == 0:
                self.close()
                raise PS4DebugError("o ps4debug fechou a conexão")
            got += n
        return bytes(buf)

    def status(self, cmd: int) -> None:
        status = U32.unpack(self.recv(4))[0]
        if status != Status.SUCCESS:
            self.close()  # o payload já está fechando do lado dele
            raise StatusError(cmd, status)

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


class PS4Debug:
    """Comandos de processo/kernel/console numa conexão própria, com lock (thread-safe)."""

    def __init__(self, host: str, port: int = 744, timeout: float = 10.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._conn: Connection | None = None
        self._lock = threading.RLock()
        self._maps: dict[int, tuple[float, list[MapEntry]]] = {}

    def _run(self, fn, idempotent: bool = True):
        with self._lock:
            for attempt in (0, 1):
                fresh = self._conn is None or not self._conn.alive
                if fresh:
                    self._conn = Connection(self.host, self.port, self.timeout)
                try:
                    return fn(self._conn)
                except StatusError:
                    self._conn = None
                    raise
                except (OSError, PS4DebugError) as e:
                    self._drop()
                    # Conexão reaproveitada pode ter caído sem aviso: repete uma vez se for seguro.
                    if attempt or fresh or not idempotent:
                        if isinstance(e, PS4DebugError):
                            raise
                        raise PS4DebugError(f"falha de rede com o ps4debug: {e}") from e

    @property
    def connected(self) -> bool:
        return bool(self._conn and self._conn.alive)

    def _drop(self) -> None:
        if self._conn:
            self._conn.close()
        self._conn = None

    def close(self) -> None:
        with self._lock:
            self._drop()

    def version(self) -> str:
        def go(c: Connection) -> str:
            c.send(Cmd.VERSION)  # sem status (server.c: handle_version)
            size = U32.unpack(c.recv(4))[0]
            return c.recv(size).decode("ascii", "replace")

        return self._run(go)

    def processes(self) -> list[tuple[int, str]]:
        def go(c: Connection):
            c.send(Cmd.PROC_LIST)
            c.status(Cmd.PROC_LIST)
            count = U32.unpack(c.recv(4))[0]
            raw = c.recv(count * PROC_LIST_ENTRY.size)
            return [
                (pid, cstr(name))
                for name, pid in PROC_LIST_ENTRY.iter_unpack(raw)
            ]

        return self._run(go)

    def process_info(self, pid: int) -> dict:
        def go(c: Connection):
            c.send(Cmd.PROC_INFO, PID.pack(pid))
            c.status(Cmd.PROC_INFO)
            rpid, name, path, titleid, contentid = PROC_INFO.unpack(c.recv(PROC_INFO.size))
            return {"pid": rpid, "name": cstr(name), "path": cstr(path), "titleid": cstr(titleid), "contentid": cstr(contentid)}

        return self._run(go)

    def maps(self, pid: int, refresh: bool = False) -> list[MapEntry]:
        cached = self._maps.get(pid)
        if cached and not refresh and time.monotonic() - cached[0] < MAPS_TTL_S:
            return cached[1]

        def go(c: Connection):
            c.send(Cmd.PROC_MAPS, PID.pack(pid))
            c.status(Cmd.PROC_MAPS)
            count = U32.unpack(c.recv(4))[0]
            raw = c.recv(count * MAP_ENTRY.size)
            entries = [
                MapEntry(cstr(name), start, end, offset, prot)
                for name, start, end, offset, prot in MAP_ENTRY.iter_unpack(raw)
            ]
            # O kernel do payload começa a varredura no cabeçalho (sentinela) do vm_map: a 1ª
            # entrada é ("", 0x4000, 0x800000000000, prot 0), que cobriria todo endereço.
            return [m for m in entries if not (not m.name and not m.prot and m.size >= 1 << 40)]

        try:
            entries = self._run(go)
        except StatusError as e:
            raise PS4DebugError(f"PROC_MAPS falhou para o pid {pid}: o processo existe? ({e})") from e
        self._maps[pid] = (time.monotonic(), entries)
        return entries

    def read(self, pid: int, address: int, size: int) -> bytes:
        if size <= 0:
            return b""

        def go(c: Connection):
            c.send(Cmd.PROC_READ, PROC_RW.pack(pid, address, size))
            c.status(Cmd.PROC_READ)
            return c.recv(size)

        return self._run(go)

    def write(self, pid: int, address: int, data: bytes) -> None:
        def go(c: Connection):
            c.send(Cmd.PROC_WRITE, PROC_RW.pack(pid, address, len(data)))
            c.status(Cmd.PROC_WRITE)
            c.send_raw(data)
            c.status(Cmd.PROC_WRITE)

        self._run(go, idempotent=False)

    # --- RPC dentro do processo (proc.c: install/call/alloc/free) ---------------------------
    # Nenhum destes é repetido: repetir aloca de novo ou executa a função duas vezes.

    def proc_alloc(self, pid: int, length: int) -> int:
        """Região rwx nova no processo. Em falha o payload devolve lixo: confira nos mapas."""
        def go(c: Connection):
            c.send(Cmd.PROC_ALLOC, PROC_ALLOC.pack(pid, length))
            c.status(Cmd.PROC_ALLOC)
            return U64.unpack(c.recv(8))[0]

        self._maps.pop(pid, None)
        return self._run(go, idempotent=False)

    def proc_free(self, pid: int, address: int, length: int) -> None:
        def go(c: Connection):
            c.send(Cmd.PROC_FREE, PROC_FREE.pack(pid, address, length))
            c.status(Cmd.PROC_FREE)

        self._maps.pop(pid, None)
        self._run(go, idempotent=False)

    def proc_install(self, pid: int) -> int:
        """Copia o stub de RPC para o processo e cria a thread dele; devolve o endereço do stub.
        Sem libkernel*.sprx r-x no processo, a thread NÃO é criada e o status ainda é sucesso."""
        def go(c: Connection):
            c.send(Cmd.PROC_INSTALL, PID.pack(pid))
            c.status(Cmd.PROC_INSTALL)
            return U64.unpack(c.recv(8))[0]

        self._maps.pop(pid, None)
        return self._run(go, idempotent=False)

    def proc_call(self, pid: int, stub: int, rip: int, *args: int) -> int:
        """Chama rip(rdi..r9) na thread do stub e devolve rax.
        PERIGO: o kernel espera rpc_done em laço. Se a thread do stub não roda (não existe, processo
        parado por SIGSTOP/breakpoint), o payload trava até reiniciar o console. Quem chama confere."""
        regs = [a & (1 << 64) - 1 for a in args] + [0] * (6 - len(args))

        def go(c: Connection):
            c.send(Cmd.PROC_CALL, PROC_CALL.pack(pid, stub, rip, *regs))
            c.status(Cmd.PROC_CALL)
            return PROC_CALL_RESP.unpack(c.recv(PROC_CALL_RESP.size))[1]

        return self._run(go, idempotent=False)

    def kernel_base(self) -> int:
        def go(c: Connection):
            c.send(Cmd.KERN_BASE)
            c.status(Cmd.KERN_BASE)
            return U64.unpack(c.recv(8))[0]

        return self._run(go)

    def kernel_read(self, address: int, size: int) -> bytes:
        def go(c: Connection):
            c.send(Cmd.KERN_READ, KERN_READ.pack(address, size))
            c.status(Cmd.KERN_READ)
            return c.recv(size)

        return self._run(go, idempotent=False)

    def notify(self, text: str, message_type: int = 222) -> None:
        """Notificação do sistema na tela do PS4 (aparece por cima do jogo)."""
        raw = text.encode("utf-8")[:1000] + b"\0"

        def go(c: Connection):
            c.send(Cmd.CONSOLE_NOTIFY, NOTIFY.pack(message_type, len(raw)))
            c.send_raw(raw)
            c.status(Cmd.CONSOLE_NOTIFY)

        self._run(go, idempotent=False)


# --- endereços --------------------------------------------------------------------------

def find_map(maps: list[MapEntry], address: int) -> MapEntry | None:
    return next((m for m in maps if m.contains(address)), None)


def module_base(maps: list[MapEntry], name: str) -> int | None:
    starts = [m.start for m in maps if m.name == name] or [m.start for m in maps if m.name.lower() == name.lower()]
    if not starts and name.lower() == "eboot.bin":  # ps4debug 1.3 no FW 12.52 chama a imagem principal de "executable"
        return module_base(maps, "executable")
    return min(starts) if starts else None


def parse_int(text: str | int) -> int:
    if isinstance(text, int):
        return text
    t = text.strip().lower().replace("_", "")
    if t.startswith(("0x", "-0x")):
        return int(t, 16)
    if t.isdigit():
        return int(t)
    raise ValueError(f"número inválido: {text!r} (use 0x... para hexadecimal)")


def parse_address(expr: str | int, maps: list[MapEntry] | None = None) -> int:
    """Aceita 0x412abc, 4271804, "eboot.bin+0x12abc" ou "eboot.bin" (base do módulo)."""
    if isinstance(expr, int):
        return expr
    text = expr.strip()
    if "+" in text or not (text[:2].lower() == "0x" or text.isdigit()):
        name, _, off = text.partition("+")
        if maps is None:
            raise ValueError(f"'{text}' é relativo a módulo; preciso dos mapas do processo")
        base = module_base(maps, name.strip())
        if base is None:
            names = sorted({m.name for m in maps if m.prot & 4})[:15]
            raise ValueError(f"módulo '{name.strip()}' não encontrado. Módulos executáveis: {', '.join(names)}")
        return base + (parse_int(off) if off else 0)
    return parse_int(text)


def describe_address(maps: list[MapEntry], address: int) -> str:
    region = find_map(maps, address)
    if not region:
        return f"{address:#x} (não mapeado)"
    base = module_base(maps, region.name)
    return f"{region.name}+{address - base:#x}" if region.name and base is not None else f"{address:#x}"


def unmapped_warning(maps: list[MapEntry], address: int, size: int) -> str | None:
    """O payload devolve zeros fora das regiões mapeadas; avisa para o agente não se enganar."""
    end, cur = address + size, address
    while cur < end:
        region = find_map(maps, cur)
        if not region:
            return f"atenção: {cur:#x} não está mapeado; o ps4debug devolve zeros nesse trecho"
        if not region.prot & 1:
            return f"atenção: {cur:#x} está numa região sem leitura ({region.name})"
        cur = region.end
    return None
