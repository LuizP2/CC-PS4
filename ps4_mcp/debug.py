"""Sessão de debug: attach, breakpoints, watchpoints, controle do processo e fila de eventos.

Como o ps4debug funciona (debug.c / server.c) e o que isso impõe aqui:
- no ATTACH o PS4 conecta DE VOLTA no PC, porta 755, antes de responder. Por isso abrimos o
  listener antes de enviar o comando. Se essa conexão falhar, o payload fica preso em
  "debugging" até ser recarregado (connect_debugger liga g_debugging antes de falhar);
- as interrupções só são verificadas no laço da conexão que fez o ATTACH, e fechar essa
  conexão desfaz o debug (free_client → debug_cleanup). Ela fica dedicada e ociosa;
- um comando de debug que falha derruba a conexão em que foi enviado. Os comandos usam uma
  segunda conexão, então um erro não mata a sessão;
- comandos de debug sem sessão ativa derrubam o payload (curdbgctx nulo): bloqueamos aqui;
- breakpoint de software: o payload restaura o byte, volta o rip, executa a instrução em
  single-step, rearma o 0xCC e só então avisa, com os registradores de ANTES da instrução.
"""

from __future__ import annotations

import errno
import select
import socket
import threading
import time
from collections import deque

from .client import Connection, PS4Debug, PS4DebugError, StatusError, find_map
from .config import Config
from .protocol import (
    BREAKPT, CRASH_SIGNALS, DR7_LEN, DR7_TYPE, I32, INTERRUPT_SIZE, PID, PROT_EXEC, REGS,
    SIGTRAP, THRINFO, U32, WATCHPT, Cmd, Status, cstr, parse_interrupt, parse_regs,
    signal_name, wait_status,
)

ATTACH_TIMEOUT_S = 25.0


def _bind_hint(port: int, err: OSError) -> str:
    if err.errno in (errno.EACCES, errno.EPERM):
        return (
            f"sem permissão para escutar na porta {port} do PC (no Linux, portas < 1024 exigem privilégio). "
            f"Rode uma vez: sudo sysctl -w net.ipv4.ip_unprivileged_port_start={port}  "
            f"(permanente: echo 'net.ipv4.ip_unprivileged_port_start={port}' | sudo tee /etc/sysctl.d/99-ps4debug.conf)"
        )
    if err.errno == errno.EADDRINUSE:
        return f"a porta {port} do PC já está em uso (outra ferramenta de debug do PS4 aberta?)"
    return f"não consegui escutar na porta {port}: {err}"


class DebugSession:
    def __init__(self, cfg: Config, client: PS4Debug):
        self.cfg, self.client = cfg, client
        self._lock = threading.RLock()
        self._cond = threading.Condition()
        self._events: deque[dict] = deque(maxlen=100)
        self.dropped_events = 0
        self._reset()

    def _reset(self) -> None:
        self.pid: int | None = None
        self.state = "detached"  # detached | running | stopped
        self.breakpoints: dict[int, dict] = {}
        self.watchpoints: dict[int, dict] = {}
        self.last_event: dict | None = None
        self._owner: Connection | None = None
        self._cmdconn: Connection | None = None
        self._debug_sock: socket.socket | None = None
        self._remote_alive = False
        self._pending_step = False
        self._detaching = False

    @property
    def attached(self) -> bool:
        return self.pid is not None

    @property
    def pending_events(self) -> int:
        return len(self._events)

    def summary(self) -> dict:
        return {
            "attached_pid": self.pid,
            "state": self.state,
            "breakpoints": [{k: v for k, v in bp.items() if k != "original"} | {"address": hex(bp["address"])} for bp in self.breakpoints.values()],
            "watchpoints": [wp | {"address": hex(wp["address"])} for wp in self.watchpoints.values()],
            "pending_events": len(self._events),
        }

    # --- attach / detach ----------------------------------------------------------------

    def attach(self, pid: int) -> dict:
        with self._lock:
            if self.attached:
                raise PS4DebugError(f"já existe sessão de debug no pid {self.pid}; use detach() antes")
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((self.cfg.callback_bind, self.cfg.callback_port))
                listener.listen(1)
            except OSError as e:
                listener.close()
                raise PS4DebugError(_bind_hint(self.cfg.callback_port, e)) from e

            owner = dbg = None
            try:
                owner = Connection(self.cfg.ps4_ip, self.cfg.debug_port, timeout=ATTACH_TIMEOUT_S)
                owner.send(Cmd.DEBUG_ATTACH, PID.pack(pid))
                status = None
                deadline = time.monotonic() + ATTACH_TIMEOUT_S
                while status is None and time.monotonic() < deadline:
                    ready, _, _ = select.select([listener, owner.sock] if dbg is None else [owner.sock], [], [], 0.5)
                    if listener in ready and dbg is None:
                        dbg, _ = listener.accept()
                    if owner.sock in ready:
                        status = U32.unpack(owner.recv(4))[0]
                if status is None:
                    raise PS4DebugError("o ps4debug não respondeu ao ATTACH a tempo")
                if status != Status.SUCCESS:
                    raise self._attach_error(StatusError(Cmd.DEBUG_ATTACH, status), dbg is not None)
                if dbg is None:  # o payload só responde SUCCESS depois de conectar, mas por via das dúvidas
                    listener.settimeout(5)
                    dbg, _ = listener.accept()
            except BaseException:
                for s in (owner, dbg):
                    if s:
                        s.close()
                raise
            finally:
                listener.close()

            dbg.settimeout(None)
            self._reset()
            self.pid, self.state = pid, "running"
            self._owner, self._debug_sock, self._remote_alive = owner, dbg, True
            with self._cond:
                self._events.clear()
            threading.Thread(target=self._read_loop, args=(dbg,), daemon=True, name="ps4-interrupts").start()
            return {"attached_pid": pid, "state": "running"}

    @staticmethod
    def _attach_error(err: StatusError, connected_back: bool) -> PS4DebugError:
        if err.status == Status.ALREADY_DEBUG:
            return PS4DebugError(
                "o ps4debug diz que já está debugando. Ou outra ferramenta está anexada, ou uma tentativa "
                "anterior falhou e deixou o payload preso (bug conhecido do ps4debug). Feche outras ferramentas; "
                "se persistir, reinicie o console e recarregue o payload."
            )
        if not connected_back:
            return PS4DebugError(
                "o PS4 não conseguiu conectar de volta na porta 755 do PC (firewall do PC bloqueando TCP 755 "
                "vindo do PS4? IP do PC mudou?). ATENÇÃO: depois desta falha o ps4debug fica preso em "
                "'debugging'; reinicie o console e recarregue o payload antes de tentar de novo. "
                "Rode ps4_diagnose para checar porta e firewall."
            )
        return PS4DebugError(f"ATTACH recusado: {err}. O pid existe? (list_processes)")

    def detach(self) -> dict:
        with self._lock:
            if not self.attached:
                return {"detached": False, "reason": "não havia sessão de debug"}
            self._detaching = True
            owner, remote_alive = self._owner, self._remote_alive
            if owner and remote_alive:
                try:
                    owner.send(Cmd.DEBUG_DETACH)
                    owner.status(Cmd.DEBUG_DETACH)
                except (OSError, PS4DebugError):
                    pass  # fechar a conexão dona também faz o payload limpar a sessão
            self._close_all()
            pid = self.pid
            self._reset()
            return {"detached": True, "pid": pid}

    def _close_all(self) -> None:
        for conn in (self._owner, self._cmdconn):
            if conn:
                conn.close()
        if self._debug_sock:
            try:
                self._debug_sock.close()
            except OSError:
                pass

    # --- interrupções ---------------------------------------------------------------------

    def _read_loop(self, sock: socket.socket) -> None:
        while True:
            try:
                raw = _recv_exact(sock, INTERRUPT_SIZE)
            except OSError:
                raw = None
            if raw is None:
                break
            try:
                intr = parse_interrupt(raw)
            except ValueError:
                continue
            with self._lock:
                if self._debug_sock is not sock:
                    return
                event = self._classify(intr)
                self.state = "stopped"
                self.last_event = event
            self._push(event)
        with self._lock:
            if self._debug_sock is not sock or self._detaching:
                return
            # O payload limpou a sessão (processo morreu ou detach do lado dele). Não mande
            # mais comandos de debug: sem sessão, o payload acessa ponteiro nulo.
            pid = self.pid
            self._remote_alive = False
            self._close_all()
            self._reset()
        self._push({"event": "detached", "pid": pid, "reason": "o ps4debug encerrou a sessão (processo terminou ou foi morto)"})

    def _classify(self, intr) -> dict:
        kind, sig = wait_status(intr.status)
        rip = intr.regs["rip"]
        event = {
            "event": "signal",
            "signal": signal_name(sig) if kind == "stopped" else f"{kind}:{sig}",
            "pid": self.pid,
            "thread_id": intr.lwpid,
            "thread_name": intr.thread_name,
            "regs": intr.regs,
            "dr6": intr.dbregs[6],
            "dr7": intr.dbregs[7],
            "received_at": time.time(),
        }
        bp = next((b for b in self.breakpoints.values() if b["address"] == rip), None)
        hit_watch = [i for i in self.watchpoints if intr.dbregs[6] & (1 << i)]
        if kind == "stopped" and sig == SIGTRAP:
            if bp:
                bp["hits"] += 1
                event |= {"event": "breakpoint", "breakpoint_id": bp["id"]}
            elif hit_watch:
                wp = self.watchpoints[hit_watch[0]]
                wp["hits"] += 1
                event |= {"event": "watchpoint", "watchpoint_id": wp["id"]}
            elif self._pending_step:
                event["event"] = "step"
            else:
                event["event"] = "trap"
        elif kind == "stopped" and sig in CRASH_SIGNALS:
            event["event"] = "crash"
        self._pending_step = False
        return event

    def _push(self, event: dict) -> None:
        with self._cond:
            if len(self._events) == self._events.maxlen:
                self.dropped_events += 1
            self._events.append(event)
            self._cond.notify_all()

    def poll_event(self) -> dict | None:
        with self._cond:
            return self._events.popleft() if self._events else None

    def wait_event(self, timeout: float) -> dict | None:
        with self._cond:
            self._cond.wait_for(lambda: bool(self._events), timeout)
            return self._events.popleft() if self._events else None

    # --- comandos ----------------------------------------------------------------------

    def _require(self, stopped: bool = False) -> None:
        if not self.attached or not self._remote_alive:
            raise PS4DebugError("não há sessão de debug ativa; use attach(pid) primeiro")
        if stopped and self.state != "stopped":
            raise PS4DebugError("o processo está rodando; use pause() ou espere um evento (wait_for_event)")

    def _cmd(self, cmd: int, payload: bytes = b"", reply=None):
        """Comando de debug na conexão auxiliar. Sem repetição automática: repetir um BREAKPT
        que chegou ao payload faria ele salvar 0xCC como byte original e corromper o código."""
        with self._lock:
            if self._cmdconn is None or not self._cmdconn.alive:
                self._cmdconn = Connection(self.cfg.ps4_ip, self.cfg.debug_port)
            conn = self._cmdconn
            try:
                conn.send(cmd, payload)
                conn.status(cmd)
                return reply(conn) if reply else None
            except (OSError, PS4DebugError) as e:
                conn.close()
                self._cmdconn = None
                if isinstance(e, PS4DebugError):
                    raise
                raise PS4DebugError(f"falha de rede no comando de debug: {e}") from e

    def set_breakpoint(self, address: int, label: str = "") -> dict:
        with self._lock:
            self._require()
            dup = next((b for b in self.breakpoints.values() if b["address"] == address), None)
            if dup:
                raise PS4DebugError(f"já existe o breakpoint #{dup['id']} em {address:#x}")
            free = next((i for i in range(self.cfg.max_breakpoints) if i not in self.breakpoints), None)
            if free is None:
                raise PS4DebugError(f"limite de {self.cfg.max_breakpoints} breakpoints atingido (PS4_MAX_BREAKPOINTS); remova um")
            region = find_map(self.client.maps(self.pid, refresh=True), address)
            if not region or not region.prot & PROT_EXEC:
                where = f"região {region.name} sem permissão de execução" if region else "endereço não mapeado"
                raise PS4DebugError(f"{address:#x}: {where}. Breakpoint de software escreve 0xCC e só serve em código; para dados use set_watchpoint")
            original = self.client.read(self.pid, address, 1)[0]
            if original == 0xCC:
                raise PS4DebugError(
                    f"já há 0xCC em {address:#x} (breakpoint antigo de outra sessão ou int3 do próprio código). "
                    "Armar outro breakpoint aí faria o payload guardar 0xCC como byte original"
                )
            self._cmd(Cmd.DEBUG_BREAKPT, BREAKPT.pack(free, 1, address))
            bp = {"id": free, "address": address, "original": original, "label": label, "hits": 0, "region": region.name}
            self.breakpoints[free] = bp
            return bp

    def clear_breakpoint(self, bp_id: int) -> dict:
        with self._lock:
            self._require()
            if bp_id not in self.breakpoints:
                raise PS4DebugError(f"breakpoint #{bp_id} não existe")
            self._cmd(Cmd.DEBUG_BREAKPT, BREAKPT.pack(bp_id, 0, 0))
            return self.breakpoints.pop(bp_id)

    def set_watchpoint(self, address: int, size: int, mode: str) -> dict:
        with self._lock:
            self._require()
            if size not in DR7_LEN:
                raise PS4DebugError("size deve ser 1, 2, 4 ou 8")
            if mode not in DR7_TYPE:
                raise PS4DebugError("mode deve ser 'write', 'rw' ou 'exec'")
            if mode == "exec" and size != 1:
                raise PS4DebugError("watchpoint de execução exige size=1")
            if address % size:
                raise PS4DebugError(f"{address:#x} não está alinhado a {size} bytes (exigência dos registradores de debug x86)")
            free = next((i for i in range(4) if i not in self.watchpoints), None)
            if free is None:
                raise PS4DebugError("os 4 registradores de debug (DR0–DR3) estão em uso; remova um watchpoint")
            self._cmd(Cmd.DEBUG_WATCHPT, WATCHPT.pack(free, 1, DR7_LEN[size], DR7_TYPE[mode], address))
            wp = {"id": free, "address": address, "size": size, "mode": mode, "hits": 0}
            self.watchpoints[free] = wp
            return wp

    def clear_watchpoint(self, wp_id: int) -> dict:
        with self._lock:
            self._require()
            if wp_id not in self.watchpoints:
                raise PS4DebugError(f"watchpoint #{wp_id} não existe")
            self._cmd(Cmd.DEBUG_WATCHPT, WATCHPT.pack(wp_id, 0, 0, 0, 0))
            return self.watchpoints.pop(wp_id)

    def threads(self) -> list[int]:
        self._require()

        def reply(c: Connection):
            count = I32.unpack(c.recv(4))[0]
            raw = c.recv(4 * count)
            return [U32.unpack_from(raw, 4 * i)[0] for i in range(count)]

        return self._cmd(Cmd.DEBUG_THREADS, reply=reply)

    def thread_info(self, lwpid: int) -> dict:
        self._require()

        def reply(c: Connection):
            tid, priority, name = THRINFO.unpack(c.recv(THRINFO.size))
            return {"thread_id": tid, "priority": priority, "name": cstr(name)}

        return self._cmd(Cmd.DEBUG_THRINFO, PID.pack(lwpid), reply)

    def get_registers(self, lwpid: int) -> dict[str, int]:
        self._require(stopped=True)
        return self._cmd(Cmd.DEBUG_GETREGS, PID.pack(lwpid), lambda c: parse_regs(c.recv(REGS.size)))

    def resume(self) -> dict:
        with self._lock:
            self._require()
            if self.state == "running":
                return {"state": "running", "note": "já estava rodando"}
            self._cmd(Cmd.DEBUG_STOPGO, U32.pack(0))
            self.state = "running"
            return {"state": "running"}

    def pause(self) -> dict:
        with self._lock:
            self._require()
            if self.state == "stopped":
                return {"state": "stopped", "note": "já estava pausado"}
            # SIGSTOP: o payload não manda pacote de interrupção para ele (server.c)
            self._cmd(Cmd.DEBUG_STOPGO, U32.pack(1))
            self.state = "stopped"
        time.sleep(0.3)
        return {"state": "stopped"}

    def step(self) -> None:
        with self._lock:
            self._require(stopped=True)
            self._pending_step = True
            try:
                self._cmd(Cmd.DEBUG_SINGLESTEP)
            except PS4DebugError:
                self._pending_step = False
                raise
            self.state = "running"

    def kill(self) -> dict:
        with self._lock:
            self._require()
            self._cmd(Cmd.DEBUG_STOPGO, U32.pack(2))
            return {"killed_pid": self.pid}


def _recv_exact(sock: socket.socket, size: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)
