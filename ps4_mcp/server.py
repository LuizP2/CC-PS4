"""Servidor MCP do PS4 Agent: as ferramentas das seções 4 e 11 do plano para o Claude Code.

Nada de rede acontece na importação: a conexão com o console é aberta na primeira ferramenta.
Ferramentas de escrita (PS4_ENABLE_WRITE=1) e de deploy (PS4_ENABLE_DEPLOY=1) só existem se
habilitadas, e as perigosas pedem confirmação manual por chamada (elicitation do MCP).
"""

import asyncio
import functools
import hashlib
import json
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import text as tx
from .bridge import Bridge
from .client import (
    PS4Debug, PS4DebugError, describe_address, find_map, module_base, parse_address, parse_int,
    unmapped_warning,
)
from .config import Config
from .debug import DebugSession
from .deploy import DeployError, build_homebrew as _build_homebrew, install_pkg as _install_pkg, local_ip_for
from .elfdump import build_elf
from .ftp import ConsoleFTP, FTPError
from .logs import LogReader
from .protocol import (
    PROT_EXEC, PROT_READ, PROT_WRITE, RPCSTUB_CODE_LEN, RPCSTUB_CODE_OFF, RPCSTUB_CODE_SHA256, RPCSTUB_MAGIC,
    RPCSTUB_USLEEP_OFF, prot_str,
)
from .safety import Audit, SafetyError, check_lan, clamp
from .sfo import parse_sfo
from .snapshot import build_snapshot, disasm

CFG = Config.from_env()

INSTRUCTIONS = """\
Ferramentas para investigar um PS4 desbloqueado em tempo de execução: memória e debug (ps4debug),
arquivos (FTP do GoldHEN) e logs do console.
Fluxo típico: ps4_status → attach(pid) → set_breakpoint("eboot.bin+0x...") ou set_watchpoint →
wait_for_event (repita após timeout) → com o processo PAUSADO: read_memory, read_pointer_chain,
disassemble, get_registers, list_threads → resume() ou step().
Endereços aceitam 0x..., decimal ou "módulo+0xdesloc". module_offset = endereço − base do módulo:
para ver pseudocódigo, gere dump_module e importe o ELF no ReVa (Ghidra); os endereços batem.
Leituras grandes viram arquivo em dumps/ + resumo, para economizar contexto.
Número na memória (moedas, vida): scan_value → peça ao usuário para mudar o valor no jogo → rescan
até sobrar poucos candidatos. Não varra de novo: o rescan relê só os candidatos.
Problema de conexão ou de porta 755: ps4_diagnose. Marque marcos com mark_milestone (métricas da fase 5).
"""

mcp = FastMCP("ps4", instructions=INSTRUCTIONS)
th = asyncio.to_thread


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return str(n)


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = PS4Debug(cfg.ps4_ip, cfg.debug_port)
        self.debug = DebugSession(cfg, self.client)
        self.ftp = ConsoleFTP(cfg.ps4_ip, cfg.ftp_port, cfg.ftp_user, cfg.ftp_pass)
        self.logs = LogReader(cfg.log_source)
        self.started = time.time()
        self.last_tool = ""
        self.busy = 0
        self.waiting = False
        self.attached_name = ""
        self.last_milestone: float | None = None
        self.scans: dict[int, dict] = {}  # scan_value/rescan: candidatos ficam aqui, não no contexto
        self.rpc: dict[int, dict] = {}  # pid -> stub de RPC e buffers da varredura dentro do jogo
        self.bridge: Bridge | None = None
        self.bridge_error = ""
        self._logs_started = False
        self._sfo_cache: dict[str, dict] = {}
        listeners = []
        if cfg.bridge:
            try:
                self.bridge = Bridge(cfg.bridge_port, cfg.bridge_discovery_port, cfg.ps4_ip, state_fn=self.bridge_state).start()
                listeners.append(self._feed)
            except OSError as e:
                self.bridge_error = f"bridge não iniciou na porta {cfg.bridge_port}: {e}"
        self.audit = Audit(cfg.workdir / "logs" / "audit.jsonl", listeners)

    def require_ps4(self) -> None:
        if not self.cfg.ps4_ip:
            raise ToolError("PS4 não encontrado na rede ao iniciar: ligue o console e reinicie o Claude Code, ou defina PS4_IP no ambiente")
        check_lan(self.cfg.ps4_ip, self.cfg.allow_public_ip)
        if not self._logs_started:
            self._logs_started = True
            self.logs.start()

    def bridge_state(self) -> dict:
        return {
            "agent": "waiting_event" if self.waiting else ("working" if self.busy else "idle"),
            "ps4debug": "connected" if self.client.connected or self.debug.attached else "disconnected",
            "attached_pid": self.debug.pid or "",
            "process": self.attached_name if self.debug.attached else "",
            "paused": int(self.debug.state == "stopped"),
            "pending_events": self.debug.pending_events,
            "last_tool": self.last_tool,
        }

    def _feed(self, entry: dict) -> None:
        kind, name = entry["kind"], entry["name"]
        if kind == "call":
            args = " ".join(f"{k}={v}" for k, v in (entry.get("args") or {}).items())
            self.bridge.add("tool", f"{name} {args}")
        elif kind == "result":
            self.bridge.add("result", f"{name}: ok ({entry.get('ms')} ms) {entry.get('summary', '')}")
        elif kind == "error":
            self.bridge.add("error", f"{name}: {entry.get('error')}")
        elif kind in ("event", "status", "milestone"):
            self.bridge.add("event" if kind == "event" else "status", entry.get("text", name))

    def read_sfo(self, titleid: str) -> dict:
        if titleid in self._sfo_cache:
            return self._sfo_cache[titleid]
        sfo: dict = {}
        for path in (f"/system_data/priv/appmeta/{titleid}/param.sfo", f"/user/appmeta/{titleid}/param.sfo"):
            try:
                data, _ = self.ftp.read_file(path, 64 * 1024)
                sfo = parse_sfo(data)
                break
            except ValueError:
                continue
            except FTPError as e:
                sfo = {"_erro": f"FTP: {e}"}
                if "conect" in str(e).lower():
                    break
        self._sfo_cache[titleid] = sfo
        return sfo

    def game_info(self, pid: int) -> dict:
        info = self.client.process_info(pid)
        if info.get("titleid"):
            sfo = self.read_sfo(info["titleid"])
            info |= {k.lower(): v for k, v in sfo.items() if k in ("TITLE", "APP_VER", "VERSION", "CATEGORY", "_erro")}
        return info

    def event_snapshot(self, event: dict) -> dict:
        if event["event"] == "detached":
            self.audit.record("event", "detached", text=f"sessão encerrada: {event.get('reason')}")
            return event
        snap = build_snapshot(event, self.client, self.debug.breakpoints, self.logs.tail(30))
        where = f"{snap.get('module')}+{snap.get('module_offset')}" if snap.get("module") else snap["rip"]
        self.audit.record("event", event["event"], text=f"{event['event']} {snap.get('signal', '')} em {where} (thread {snap.get('thread_id')})")
        return snap

    def shutdown(self) -> None:
        for step in (self.debug.detach, self.logs.stop, self.ftp.close, self.client.close):
            try:
                step()
            except Exception:
                pass
        if self.bridge:
            self.bridge.stop()


_app: App | None = None


def get_app() -> App:
    global _app
    if _app is None:
        _app = App(CFG)
    return _app


TOOL_ERRORS = (PS4DebugError, FTPError, SafetyError, DeployError, ValueError, OSError)


def ps4_tool(readonly: bool = True, needs_ps4: bool = True, destructive: bool = False):
    """Registra a ferramenta com auditoria, tradução de erros e resposta compacta."""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(**kwargs):
            a = get_app()
            a.audit.record("call", fn.__name__, {k: v for k, v in kwargs.items() if k != "ctx"})
            a.busy += 1
            a.last_tool = fn.__name__
            t0 = time.perf_counter()
            try:
                if needs_ps4:
                    a.require_ps4()
                result = await fn(**kwargs)
            except Exception as e:
                a.audit.record("error", fn.__name__, ms=round((time.perf_counter() - t0) * 1000), error=str(e))
                if isinstance(e, ToolError):
                    raise
                if isinstance(e, TOOL_ERRORS):
                    raise ToolError(str(e)) from e
                raise
            finally:
                a.busy -= 1
            out = result if isinstance(result, str) else _j(result)
            a.audit.record("result", fn.__name__, ms=round((time.perf_counter() - t0) * 1000), summary=out[:120], size=len(out))
            return out

        annotations = ToolAnnotations(readOnlyHint=readonly, destructiveHint=destructive, openWorldHint=False)
        mcp.tool(annotations=annotations, structured_output=False)(wrapper)
        return wrapper

    return deco


class Confirmacao(BaseModel):
    confirmar: bool = False


async def confirm(ctx: Context | None, message: str) -> None:
    """Confirmação manual por chamada (seção 4.3): pergunta ao usuário pelo cliente MCP."""
    if CFG.write_confirm == "none":
        return
    try:
        result = await ctx.elicit(message=message, schema=Confirmacao)
    except Exception as e:
        raise ToolError(
            f"este cliente MCP não ofereceu confirmação manual ({e}); operação bloqueada. "
            "PS4_WRITE_CONFIRM=none passa a confiar só no prompt de permissão do Claude Code"
        ) from e
    if result.action != "accept" or not getattr(result.data, "confirmar", False):
        raise ToolError("operação cancelada pelo usuário")


# --- visão geral ----------------------------------------------------------------------------

@ps4_tool()
async def ps4_status() -> str:
    """Comece por aqui. ps4debug conectado e versão, jogos em execução (pid, title id, título, versão),
    sessão de debug (breakpoints, watchpoints, eventos pendentes) e estado dos logs."""
    a = get_app()
    out: dict = {"ps4_ip": a.cfg.ps4_ip}
    try:
        out["ps4debug"] = {"connected": True, "version": await th(a.client.version)}
        procs = await th(a.client.processes)
        out["processes"] = len(procs)
        out["games"] = [await th(a.game_info, pid) for pid, name in procs if name == "eboot.bin"]
    except PS4DebugError as e:
        out["ps4debug"] = {"connected": False, "error": str(e)}
    out["debug"] = a.debug.summary()
    out["logs"] = a.logs.status()
    if a.bridge or a.bridge_error:
        out["bridge"] = {"port": a.bridge.port, "messages_pending": a.bridge.has_messages()} if a.bridge else {"error": a.bridge_error}
    return out


@ps4_tool(needs_ps4=False)
async def ps4_diagnose() -> str:
    """Checa tudo o que costuma dar errado: IP, ps4debug (744), FTP, logs, porta 755 do PC
    (callback do debugger), firewall e rota. Use quando algo não conectar."""
    a, cfg, checks = get_app(), CFG, []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    if not cfg.ps4_ip:
        add("PS4_IP", False, "PS4 não encontrado na rede ao iniciar (DDP na porta 987 e varredura da porta 744). Ligue o console e reinicie, ou defina PS4_IP")
        return {"checks": checks}
    try:
        check_lan(cfg.ps4_ip, cfg.allow_public_ip)
        add("PS4_IP", True, cfg.ps4_ip)
    except SafetyError as e:
        add("PS4_IP", False, str(e))
        return {"checks": checks}
    a.require_ps4()
    t0 = time.perf_counter()
    try:
        version = await th(a.client.version)
        add("ps4debug", True, f"versão {version} em {cfg.ps4_ip}:{cfg.debug_port} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    except PS4DebugError as e:
        add("ps4debug", False, str(e))
    try:
        entries = await th(a.ftp.list_dir, "/")
        add("ftp", True, f"{cfg.ps4_ip}:{cfg.ftp_port}, {len(entries)} itens em /")
    except FTPError as e:
        add("ftp", False, str(e))
    await asyncio.sleep(0.5)
    st = a.logs.status()
    add("logs", bool(st.get("connected") or st.get("lines")), f"{cfg.log_source}: {st}")
    if a.debug.attached:
        add("porta 755", True, "em uso pela sessão de debug atual")
    else:
        try:
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((cfg.callback_bind, cfg.callback_port))
            add(f"porta {cfg.callback_port}", True, "o PC consegue escutar as interrupções do debugger")
        except OSError as e:
            from .debug import _bind_hint
            add(f"porta {cfg.callback_port}", False, _bind_hint(cfg.callback_port, e))
    try:
        start = Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text().strip()
        add("ip_unprivileged_port_start", int(start) <= cfg.callback_port, f"valor atual {start} (precisa ser <= {cfg.callback_port} para escutar sem root)")
    except (OSError, ValueError):
        pass
    for cmd in (["firewall-cmd", "--state"], ["ufw", "status"]):
        if shutil.which(cmd[0]):
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                state = (res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr).strip() else "?"
                add(f"firewall ({cmd[0]})", True, f"{state}; se ativo, libere TCP {cfg.callback_port} vindo do PS4" + (f" e TCP {cfg.bridge_port}/UDP {cfg.bridge_discovery_port}" if cfg.bridge else ""))
            except (OSError, subprocess.TimeoutExpired):
                pass
    try:
        pc_ip = local_ip_for(cfg.ps4_ip)
        add("rota", True, f"o PC alcança o PS4 pelo IP {pc_ip}; o ps4debug conecta de volta em {pc_ip}:{cfg.callback_port}")
    except OSError as e:
        add("rota", False, f"sem rota para {cfg.ps4_ip}: {e}")
    if cfg.bridge:
        add("bridge", a.bridge is not None, a.bridge_error or f"HTTP {a.bridge.port}, anúncio UDP {cfg.bridge_discovery_port}")
    return {"checks": checks}


@ps4_tool()
async def list_processes() -> str:
    """Lista os processos do PS4 (pid e nome). Jogos e homebrew aparecem como eboot.bin."""
    procs = await th(get_app().client.processes)
    lines = [f"{pid:>6}  {name}" + ("   ← jogo/homebrew" if name == "eboot.bin" else "") for pid, name in sorted(procs)]
    return "   pid  nome\n" + "\n".join(lines)


@ps4_tool()
async def process_maps(pid: int, name_filter: str = "", min_size: int = 0, limit: int = 300) -> str:
    """Regiões de memória do processo: início-fim, tamanho, proteção (rwx) e nome do módulo.
    name_filter filtra por trecho do nome; min_size por tamanho em bytes."""
    maps = await th(get_app().client.maps, pid, True)
    rows = [m for m in maps if name_filter.lower() in m.name.lower() and m.size >= min_size]
    lines = [f"{m.start:#014x}-{m.end:#014x} {_size(m.size):>9} {prot_str(m.prot):<4} {m.name}" for m in rows[:limit]]
    head = f"{len(rows)} de {len(maps)} regiões" + (f" (mostrando {limit})" if len(rows) > limit else "")
    return head + "\n" + "\n".join(lines)


# --- memória ---------------------------------------------------------------------------------

@ps4_tool()
async def read_memory(pid: int, address: str, size: int = 256) -> str:
    """Lê memória do processo. address: 0x412abc, decimal ou "eboot.bin+0x12abc". Até PS4_MAX_READ
    (64 KiB). Até 1 KiB devolve hexdump; acima disso salva em dumps/ e devolve um resumo
    (entropia, strings, ponteiros) para economizar contexto."""
    a = get_app()
    maps = await th(a.client.maps, pid)
    addr = parse_address(address, maps)
    size = clamp(size, 1, a.cfg.max_read)
    data = await th(a.client.read, pid, addr, size)
    warn = unmapped_warning(maps, addr, size)
    head = f"# pid {pid} {describe_address(maps, addr)} ({size} bytes)" + (f"\n# {warn}" if warn else "")
    if size <= a.cfg.inline_hex_max:
        return head + "\n" + tx.hexdump(data, addr)
    path = a.cfg.out_dir("dumps") / f"mem_{pid}_{addr:x}_{size}.bin"
    path.write_bytes(data)
    return {"note": "leitura grande: bytes completos salvos em arquivo; abaixo um resumo", "saved_to": str(path),
            "where": describe_address(maps, addr), "warning": warn, "summary": tx.summarize_bytes(data, addr)}


@ps4_tool()
async def read_pointer_chain(pid: int, base: str, offsets: list[str]) -> str:
    """Segue uma cadeia de ponteiros: lê o ponteiro em base; para cada offset, soma ao ponteiro
    atual e (exceto no último) lê o próximo ponteiro. Devolve cada passo, o endereço final e
    32 bytes nele. Ex.: base="eboot.bin+0x2a1000", offsets=["0x18","0x40","0x0"]."""
    a = get_app()
    maps = await th(a.client.maps, pid)
    addr = parse_address(base, maps)
    offs = [parse_int(o) for o in offsets]

    def u64(at: int) -> int:
        return int.from_bytes(a.client.read(pid, at, 8), "little")

    if not find_map(maps, addr):
        raise ToolError(f"base {addr:#x} não está mapeada")
    ptr = await th(u64, addr)
    steps = [{"read_at": hex(addr), "where": describe_address(maps, addr), "value": hex(ptr)}]
    final = ptr
    for i, off in enumerate(offs):
        target = (ptr + off) & 0xFFFFFFFFFFFFFFFF
        if i == len(offs) - 1:
            final = target
            break
        if not find_map(maps, target):
            steps.append({"read_at": hex(target), "error": "não mapeado: cadeia quebrada aqui (ponteiro nulo ou obsoleto?)"})
            return {"steps": steps, "final_address": None}
        ptr = await th(u64, target)
        steps.append({"read_at": hex(target), "value": hex(ptr)})
    out = {"steps": steps, "final_address": hex(final), "final_where": describe_address(maps, final)}
    if find_map(maps, final):
        preview = await th(a.client.read, pid, final, 32)
        out |= {"preview_hex": preview.hex(), "as_u64": hex(int.from_bytes(preview[:8], "little")),
                "as_text": tx.decode_text(preview, "utf-8", stop_at_nul=True)}
    return out


@ps4_tool()
async def read_string(pid: int, address: str, max_bytes: int = 256, encoding: str = "utf-8") -> str:
    """Lê uma string terminada em zero. encoding: utf-8, utf-16le, shift_jis, latin-1. Códigos de
    controle aparecem como <0A>, bytes inválidos como <?XX>."""
    a = get_app()
    maps = await th(a.client.maps, pid)
    addr = parse_address(address, maps)
    data = await th(a.client.read, pid, addr, clamp(max_bytes, 1, a.cfg.max_read))
    return {"address": hex(addr), "where": describe_address(maps, addr), "encoding": tx.normalize_encoding(encoding),
            "text": tx.decode_text(data, encoding, stop_at_nul=True)}


@ps4_tool()
async def resolve_address(pid: int, address: str) -> str:
    """Converte endereço de execução em módulo+deslocamento (e vice-versa, com "módulo+0x..."),
    mostrando a região e a proteção. module_offset é o que se procura no Ghidra/ReVa."""
    maps = await th(get_app().client.maps, pid, True)
    addr = parse_address(address, maps)
    region = find_map(maps, addr)
    if not region:
        return {"address": hex(addr), "mapped": False}
    base = module_base(maps, region.name)
    return {"address": hex(addr), "mapped": True, "module": region.name, "module_base": hex(base),
            "module_offset": hex(addr - base), "region": f"{region.start:#x}-{region.end:#x} {prot_str(region.prot)}"}


@ps4_tool()
async def disassemble(pid: int, address: str, count: int = 16) -> str:
    """Disassembly x86-64 a partir do endereço (sem precisar do Ghidra). Breakpoints armados
    aparecem com o byte original, não 0xCC."""
    a = get_app()
    maps = await th(a.client.maps, pid)
    addr = parse_address(address, maps)
    count = clamp(count, 1, 64)
    code = bytearray(await th(a.client.read, pid, addr, count * 15))
    if a.debug.pid == pid:
        for bp in a.debug.breakpoints.values():
            if addr <= bp["address"] < addr + len(code):
                code[bp["address"] - addr] = bp["original"]
    lines = disasm(bytes(code), addr, count)
    return f"# {describe_address(maps, addr)}\n" + "\n".join(lines)


# --- arquivos e logs ------------------------------------------------------------------------

@ps4_tool()
async def list_dir(path: str = "/") -> str:
    """Lista uma pasta do console via FTP (ex.: /mnt/sandbox, /user/app, /data). Jogos ficam em
    /mnt/sandbox/pfsmnt/CUSAxxxxx-app0 enquanto rodam."""
    entries = await th(get_app().ftp.list_dir, path)
    lines = [f"{e['type'][0]} {e['size']:>12} {e['mtime']:<12} {e['name']}" for e in entries]
    return f"{path} ({len(entries)} itens)\n" + "\n".join(lines)


@ps4_tool()
async def read_file(path: str, max_bytes: int = 65536, mode: str = "auto") -> str:
    """Lê um arquivo do console via FTP. mode: auto (texto se parecer texto, senão hex), text ou hex.
    Arquivos binários grandes vão para downloads/ com resumo. Para baixar inteiro use download_file."""
    a = get_app()
    data, truncated = await th(a.ftp.read_file, path, clamp(max_bytes, 1, 16 * 1024 * 1024))
    note = f"(lidos {len(data)} bytes{', truncado' if truncated else ''})"
    printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in data[:4096]) / max(1, min(len(data), 4096))
    if mode == "text" or (mode == "auto" and printable > 0.9):
        return f"# {path} {note}\n" + data.decode("utf-8", "replace")
    if len(data) <= a.cfg.inline_hex_max:
        return f"# {path} {note}\n" + tx.hexdump(data)
    local = a.cfg.out_dir("downloads") / ("partial_" + path.strip("/").replace("/", "__"))
    local.write_bytes(data)
    return {"path": path, "note": note, "saved_to": str(local), "summary": tx.summarize_bytes(data)}


@ps4_tool()
async def download_file(path: str, local_name: str = "") -> str:
    """Baixa um arquivo do console para downloads/ no PC (ex.: assets, saves, binários para o Ghidra).
    Atenção: eboot.bin/sprx no disco são SELF criptografados; para RE use dump_module (memória)."""
    a = get_app()
    name = Path(local_name).name if local_name else path.strip("/").replace("/", "__")
    dest = a.cfg.out_dir("downloads") / name
    return await th(a.ftp.download, path, str(dest), a.cfg.max_download_mb * 1024 * 1024)


@ps4_tool()
async def tail_logs(n: int = 50, grep: str = "") -> str:
    """Últimas N linhas de log do console (klog). grep: trecho sem diferenciar maiúsculas, ou
    "re:<regex>"."""
    a = get_app()
    lines = a.logs.tail(clamp(n, 1, 2000), grep or None)
    st = a.logs.status()
    head = f"# {st['source']} conectado={st['connected']} linhas={st['lines']}" + (f" erro={st['last_error']}" if st.get("last_error") else "")
    return head + "\n" + "\n".join(lines)


# --- debug -------------------------------------------------------------------------------------

def _require_attached() -> App:
    a = get_app()
    if not a.debug.attached:
        raise ToolError("não há sessão de debug; use attach(pid)")
    return a


@ps4_tool(readonly=False)
async def attach(pid: int = 0) -> str:
    """Inicia a sessão de debug no processo (pid=0 escolhe o único eboot.bin). O processo continua
    rodando. Requer a porta 755 do PC aberta para o PS4 (veja ps4_diagnose)."""
    a = get_app()
    if pid == 0:
        games = [p for p, n in await th(a.client.processes) if n == "eboot.bin"]
        if len(games) != 1:
            raise ToolError(f"informe o pid; processos eboot.bin encontrados: {games or 'nenhum'}")
        pid = games[0]
    try:
        info = await th(a.client.process_info, pid)
    except PS4DebugError:
        info = {}
    res = await th(a.debug.attach, pid)
    a.attached_name = info.get("name") or ""
    maps = await th(a.client.maps, pid, True)
    modules = {}
    for m in maps:
        if m.prot & PROT_EXEC and m.name and m.name not in modules:
            modules[m.name] = hex(module_base(maps, m.name))
    return res | {"process": info, "executable_modules": dict(list(modules.items())[:25]),
                  "next": "set_breakpoint / set_watchpoint e depois wait_for_event"}


@ps4_tool(readonly=False)
async def detach() -> str:
    """Encerra a sessão de debug: o ps4debug restaura os bytes dos breakpoints, limpa os
    watchpoints e deixa o processo rodando."""
    a = get_app()
    a.attached_name = ""
    return await th(a.debug.detach)


@ps4_tool(readonly=False)
async def set_breakpoint(address: str, label: str = "") -> str:
    """Breakpoint de software (0xCC) num endereço de código: "eboot.bin+0x12abc" ou 0x...
    Limite PS4_MAX_BREAKPOINTS (padrão 10). Quando disparar, o processo pausa e wait_for_event
    devolve o snapshot."""
    a = _require_attached()
    maps = await th(a.client.maps, a.debug.pid, True)
    addr = parse_address(address, maps)
    bp = await th(a.debug.set_breakpoint, addr, label)
    head = bytearray(await th(a.client.read, a.debug.pid, addr, 16))
    head[0] = bp["original"]
    return {"id": bp["id"], "address": hex(addr), "where": describe_address(maps, addr),
            "instruction": (disasm(bytes(head), addr, 1) or ["?"])[0]}


@ps4_tool(readonly=False)
async def clear_breakpoint(id: int) -> str:
    """Remove o breakpoint (o ps4debug devolve o byte original)."""
    bp = await th(_require_attached().debug.clear_breakpoint, id)
    return {"removed": id, "address": hex(bp["address"]), "hits": bp["hits"]}


@ps4_tool(readonly=False)
async def set_watchpoint(address: str, size: int = 4, mode: str = "write") -> str:
    """Watchpoint de hardware (DR0–DR3, máx. 4). mode: write, rw ou exec (exec exige size=1).
    size 1/2/4/8 e endereço alinhado ao size. Vale para as threads que já existem; threads
    criadas depois não herdam. Bom para bugs de timing: não altera o código."""
    a = _require_attached()
    maps = await th(a.client.maps, a.debug.pid, True)
    addr = parse_address(address, maps)
    wp = await th(a.debug.set_watchpoint, addr, size, mode)
    return wp | {"address": hex(addr), "where": describe_address(maps, addr)}


@ps4_tool(readonly=False)
async def clear_watchpoint(id: int) -> str:
    """Remove o watchpoint."""
    wp = await th(_require_attached().debug.clear_watchpoint, id)
    return {"removed": id, "address": hex(wp["address"]), "hits": wp["hits"]}


@ps4_tool()
async def wait_for_event(timeout_s: int = 60, ctx: Context = None) -> str:
    """Bloqueia até um breakpoint, watchpoint ou crash disparar (ou chegar mensagem do app no PS4)
    e devolve o snapshot completo: registradores, módulo+offset, pilha, código desmontado,
    palpite do endereço da falha e logs recentes. Em timeout, chame de novo."""
    a = get_app()
    if not a.debug.attached and not a.debug.pending_events and not (a.bridge and a.bridge.has_messages()):
        if not a.bridge:
            raise ToolError("não há sessão de debug (attach) nem bridge ativo: nada para esperar")
    timeout = clamp(timeout_s, 1, a.cfg.max_wait_s)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    next_progress = loop.time() + 10
    a.waiting = True
    try:
        while True:
            event = a.debug.poll_event()
            if event:
                return await th(a.event_snapshot, event)
            if a.bridge and a.bridge.has_messages():
                return {"event": "user_message", "messages": a.bridge.pop_messages()}
            now = loop.time()
            if now >= deadline:
                return {"event": "timeout", "waited_s": timeout, "debug": a.debug.summary()}
            if ctx and now >= next_progress:
                next_progress = now + 10
                try:
                    await ctx.report_progress(now - (deadline - timeout), timeout, "aguardando evento do PS4")
                except Exception:
                    pass
            await asyncio.sleep(0.2)
    finally:
        a.waiting = False


@ps4_tool()
async def get_registers(thread_id: int = 0) -> str:
    """Registradores de uma thread (thread_id=0 usa a thread do último evento). O processo
    precisa estar pausado (após um evento ou pause())."""
    a = _require_attached()
    tid = thread_id or (a.debug.last_event or {}).get("thread_id")
    if not tid:
        raise ToolError("informe thread_id (list_threads)")
    regs = await th(a.debug.get_registers, tid)
    maps = await th(a.client.maps, a.debug.pid)
    out = {k: hex(v) for k, v in regs.items() if k not in ("trapno", "err", "fs", "gs", "es", "ds", "cs", "ss")}
    out["rip_where"] = describe_address(maps, regs["rip"])
    return {"thread_id": tid, "registers": out}


@ps4_tool()
async def list_threads() -> str:
    """Threads do processo anexado (lwpid, prioridade, nome)."""
    a = _require_attached()
    tids = await th(a.debug.threads)
    current = (a.debug.last_event or {}).get("thread_id")
    lines = []
    for tid in tids[:64]:
        try:
            info = await th(a.debug.thread_info, tid)
            lines.append(f"{tid:>8}  prio={info['priority']:<4} {info['name']}" + ("   ← thread do último evento" if tid == current else ""))
        except PS4DebugError as e:
            lines.append(f"{tid:>8}  (sem info: {e})")
    more = f"\n… e mais {len(tids) - 64}" if len(tids) > 64 else ""
    return f"{len(tids)} threads\n" + "\n".join(lines) + more


@ps4_tool(readonly=False)
async def step() -> str:
    """Executa uma instrução (processo precisa estar pausado) e devolve o snapshot do novo ponto."""
    a = _require_attached()
    await th(a.debug.step)
    event = await th(a.debug.wait_event, 5.0)
    if not event:
        return {"event": "none", "note": "o step foi enviado mas nenhum evento chegou em 5 s; use wait_for_event"}
    return await th(a.event_snapshot, event)


@ps4_tool(readonly=False)
async def resume() -> str:
    """Continua a execução do processo pausado."""
    return await th(_require_attached().debug.resume)


@ps4_tool(readonly=False)
async def pause() -> str:
    """Pausa o processo (SIGSTOP) para inspecionar threads e registradores."""
    return await th(_require_attached().debug.pause)


# --- engenharia reversa --------------------------------------------------------------------

@ps4_tool()
async def dump_module(pid: int, module: str = "eboot.bin", ctx: Context = None) -> str:
    """Dumpa as regiões de um módulo (decifrado, na memória) num ELF64 com os endereços reais de
    execução. Importe no ReVa/Ghidra (import-file) e use rip/module_offset dos eventos direto."""
    a = get_app()
    maps = await th(a.client.maps, pid, True)
    regions = [m for m in maps if m.name == module and m.prot & PROT_READ]
    if not regions:
        names = sorted({m.name for m in maps if m.prot & PROT_EXEC})[:20]
        raise ToolError(f"módulo '{module}' sem regiões legíveis. Módulos executáveis: {names}")
    total = sum(m.size for m in regions)
    if total > a.cfg.max_dump_mb * 1024 * 1024:
        raise ToolError(f"módulo tem {_size(total)}, acima de PS4_MAX_DUMP_MB={a.cfg.max_dump_mb}")
    segments, done = [], 0
    for m in regions:
        chunks = []
        for pos in range(m.start, m.end, 1 << 20):
            chunks.append(await th(a.client.read, pid, pos, min(1 << 20, m.end - pos)))
            done += len(chunks[-1])
            if ctx:
                try:
                    await ctx.report_progress(done, total, f"dump de {module}")
                except Exception:
                    pass
        data = bytearray(b"".join(chunks))
        if a.debug.pid == pid:  # breakpoints armados: o ELF leva o byte original, não 0xCC
            for bp in a.debug.breakpoints.values():
                if m.start <= bp["address"] < m.end:
                    data[bp["address"] - m.start] = bp["original"]
        segments.append((m.start, bytes(data), m.prot))
    base = min(m.start for m in regions)
    path = a.cfg.out_dir("dumps") / f"{module.replace('/', '_')}_{pid}_{base:x}.elf"
    path.write_bytes(build_elf(segments))
    return {"saved_to": str(path), "module": module, "base": hex(base), "bytes": total,
            "segments": [f"{m.start:#x}-{m.end:#x} {prot_str(m.prot)}" for m in regions],
            "next": "mcp__ReVa__import-file com este caminho; endereços no Ghidra = endereços de execução"}


# --- piloto: tradução (seção 11) -----------------------------------------------------------

def _needles(text: str, encoding: str) -> list[tuple[str, bytes]]:
    encs = ["utf-8", "utf-16le"] if encoding == "auto" else [tx.normalize_encoding(encoding)]
    return [(e, tx.encode_needle(text, e)) for e in encs]


def _select_regions(maps, regions: str) -> list:
    kw = regions.lower()
    if kw == "writable":
        return [m for m in maps if m.prot & PROT_READ and m.prot & PROT_WRITE]
    if kw == "all":
        return [m for m in maps if m.prot & PROT_READ]
    if kw == "code":
        return [m for m in maps if m.prot & PROT_READ and m.prot & PROT_EXEC]
    return [m for m in maps if m.prot & PROT_READ and kw in m.name.lower()]


def _scan(a: App, pid: int, needles, regions: str, budget: int, max_results: int) -> dict:
    maps = a.client.maps(pid, refresh=True)
    selected = _select_regions(maps, regions)
    width = max(len(n) for _, n in needles)
    found: dict[tuple[int, str], dict] = {}
    scanned, skipped, t0 = 0, 0, time.perf_counter()
    for m in selected:
        if scanned >= budget or len(found) >= max_results:
            skipped += 1
            continue
        pos, tail = m.start, b""
        while pos < m.end and scanned < budget and len(found) < max_results:
            data = a.client.read(pid, pos, min(1 << 20, m.end - pos))
            scanned += len(data)
            buf, base = tail + data, pos - len(tail)
            for enc, needle in needles:
                for off in tx.find_all(buf, needle, max_results):
                    addr = base + off
                    if (addr, enc) in found:
                        continue
                    lo = max(0, off - 48 - (off % 2 if enc.startswith("utf-16") else 0))
                    ctx = tx.decode_text(buf[lo : off + len(needle) + 48], enc)
                    found[(addr, enc)] = {"address": hex(addr), "where": describe_address(maps, addr), "encoding": enc,
                                          "region": f"{m.name} {prot_str(m.prot)}", "context": ctx}
            tail = buf[-(width - 1):] if width > 1 else b""
            pos += len(data)
    return {"matches": list(found.values())[:max_results], "scanned": _size(scanned),
            "regions_scanned": len(selected) - skipped, "regions_skipped": skipped,
            "seconds": round(time.perf_counter() - t0, 1),
            "note": "busca feita no PC lendo a memória (o CMD_PROC_SCAN do ps4debug tem bugs)"}


@ps4_tool()
async def search_memory(pid: int, text: str, encoding: str = "auto", regions: str = "writable",
                        max_mb: int = 128, max_results: int = 20) -> str:
    """Procura uma frase (ex.: a legenda na tela) na memória do jogo. encoding auto = UTF-8 e
    UTF-16LE. regions: writable (heap/dados, padrão), all, code ou trecho do nome da região.
    Lê até max_mb pela rede (≈10–30 MB/s); refine com regions se for lento."""
    a = get_app()
    return await th(_scan, a, pid, _needles(text, encoding), regions, clamp(max_mb, 1, 4096) * 1024 * 1024, clamp(max_results, 1, 200))


VALUE_TYPES = {"u8": "B", "u16": "H", "u32": "I", "u64": "Q", "i8": "b", "i16": "h", "i32": "i", "i64": "q",
               "f32": "f", "f64": "d"}
MAX_CANDIDATES = 2_000_000
MAX_SCANS = 8
LIST_AT = 20  # com até isso de candidatos, a resposta já traz os endereços
HELPERS = Path(__file__).parent / "helpers"
INGAME_CAP = 1 << 19  # endereços por chamada no buffer dentro do jogo (4 MiB)
INGAME_CHUNK = 128 << 20  # memória varrida por PROC_CALL: cada chamada fica bem abaixo do timeout de 10 s


def _value_fmt(type: str) -> struct.Struct:
    if type not in VALUE_TYPES:
        raise ToolError(f"type deve ser um de {', '.join(VALUE_TYPES)}")
    return struct.Struct("<" + VALUE_TYPES[type])


def _parse_value(fmt: struct.Struct, value: str):
    if fmt.format[-1] in "fd":
        v = float(value)
    else:
        v = parse_int(value) if "0x" in value.lower() else int(value)  # int() aceita negativos decimais
    try:
        fmt.pack(v)
    except struct.error as e:
        raise ToolError(f"{value} não cabe no tipo: {e}") from e
    return v


def _scan_result(a: App, scan_id: int, scan: dict, maps, net: int, t0: float, **extra) -> dict:
    out = {"scan_id": scan_id, "candidates": len(scan["addrs"]), "read": _size(net),
           "seconds": round(time.perf_counter() - t0, 1), **extra}
    if len(scan["addrs"]) <= LIST_AT:
        out["matches"] = [{"address": hex(addr), "where": describe_address(maps, addr), "value": v}
                          for addr, v in zip(scan["addrs"], scan["vals"])]
    elif scan["addrs"]:
        out["next"] = "mude o valor no jogo e chame rescan com o valor novo (ou increased/decreased/changed/unchanged)"
    return out


def _ingame(a: App, pid: int) -> tuple[dict | None, str]:
    """Prepara helpers/scan_value.bin dentro do jogo. (None, motivo) = varrer pela rede."""
    if not a.cfg.ingame_scan:
        return None, "PS4_INGAME_SCAN=0"
    if a.debug.attached and a.debug.pid == pid:
        # breakpoint, watchpoint ou pause param a thread do stub, e o PROC_CALL travaria o payload
        return None, "processo anexado ao debugger: dê detach para varrer dentro do jogo"
    rpc = a.rpc.get(pid)
    if rpc is not None:
        return (rpc, "") if rpc["ok"] else (None, rpc["why"])
    a.rpc[pid] = rpc = {"ok": False, "why": "", "owned": set()}
    try:
        stub = a.client.proc_install(pid)
        rpc["owned"].add(stub)
        head = a.client.read(pid, stub, RPCSTUB_CODE_OFF + RPCSTUB_CODE_LEN)
        if head[:4] != RPCSTUB_MAGIC or hashlib.sha256(head[RPCSTUB_CODE_OFF:]).hexdigest() != RPCSTUB_CODE_SHA256:
            rpc["why"] = "stub de RPC diferente do conhecido nesta versão do ps4debug"
            return None, rpc["why"]
        deadline = time.monotonic() + 3
        while not int.from_bytes(a.client.read(pid, stub + RPCSTUB_USLEEP_OFF, 8), "little"):
            if time.monotonic() > deadline:  # sem a thread, o PROC_CALL trava o payload: nunca chame
                rpc["why"] = "a thread do stub de RPC não iniciou (processo sem libkernel ou parado)"
                return None, rpc["why"]
            time.sleep(0.1)
        blob = (HELPERS / "scan_value.bin").read_bytes()
        code = a.client.proc_alloc(pid, len(blob))
        out = a.client.proc_alloc(pid, INGAME_CAP * 8)
        rpc["owned"] |= {code, out}
        maps = a.client.maps(pid, refresh=True)
        if not (find_map(maps, code) and find_map(maps, out)):
            rpc["why"] = "PROC_ALLOC falhou no processo"
            return None, rpc["why"]
        a.client.write(pid, code, blob)
    except PS4DebugError as e:
        rpc["why"] = f"preparo da varredura no jogo falhou: {e}"
        return None, rpc["why"]
    rpc.update(ok=True, stub=stub, code=code, out=out)
    return rpc, ""


def _scan_ingame(a: App, pid: int, rpc: dict, regions: list, raw_value: int, size: int, budget: int):
    """Uma chamada por bloco de até INGAME_CHUNK; pela rede volta só a lista de endereços."""
    addrs, scanned, net, truncated = [], 0, 0, False
    for m in regions:
        for pos in range(m.start, m.end, INGAME_CHUNK):
            if scanned >= budget or truncated:
                break
            length = min(INGAME_CHUNK, m.end - pos)
            n = a.client.proc_call(pid, rpc["stub"], rpc["code"], pos, length, raw_value, size, rpc["out"], INGAME_CAP)
            scanned += length
            got = min(n, INGAME_CAP)
            for off in range(0, got * 8, 1 << 20):
                raw = a.client.read(pid, rpc["out"] + off, min(1 << 20, got * 8 - off))
                net += len(raw)
                addrs.extend(struct.unpack(f"<{len(raw) // 8}Q", raw))
            if n > INGAME_CAP or len(addrs) >= MAX_CANDIDATES:
                truncated = True
    return addrs[:MAX_CANDIDATES], scanned, net, truncated


def _scan_value(a: App, pid: int, fmt: struct.Struct, value, regions: str, budget: int) -> dict:
    needle, size = fmt.pack(value), fmt.size
    t0 = time.perf_counter()
    rpc, why = _ingame(a, pid)
    owned = a.rpc.get(pid, {}).get("owned", set())  # buffers do próprio MCP no jogo não entram na busca
    maps = a.client.maps(pid, refresh=True)
    selected = [m for m in _select_regions(maps, regions) if m.start not in owned]
    if rpc:
        try:
            addrs, scanned, net, truncated = _scan_ingame(a, pid, rpc, selected, int.from_bytes(needle, "little"), size, budget)
            return _finish_scan(a, pid, fmt, value, maps, addrs, net, t0, truncated, scanned >= budget, budget,
                                engine="in-game", scanned=_size(scanned))
        except PS4DebugError as e:
            rpc.update(ok=False, why=f"varredura no jogo falhou: {e}")
            why = rpc["why"]
    addrs, scanned, truncated = [], 0, False
    for m in selected:
        # shortcut: só valores alinhados ao tamanho do tipo (como o "fast scan" do Cheat Engine); blocos de 1 MiB
        # a partir do início da região (página) nunca cortam um valor alinhado ao meio.
        for pos in range(m.start, m.end, 1 << 20):
            if scanned >= budget or truncated:
                break
            data = a.client.read(pid, pos, min(1 << 20, m.end - pos))
            scanned += len(data)
            off = data.find(needle)
            while off >= 0:
                if (pos + off) % size == 0:
                    addrs.append(pos + off)
                    if len(addrs) >= MAX_CANDIDATES:
                        truncated = True
                        break
                off = data.find(needle, off + 1)
    return _finish_scan(a, pid, fmt, value, maps, addrs, scanned, t0, truncated, scanned >= budget, budget,
                        engine="network", **({"engine_note": why} if why != "PS4_INGAME_SCAN=0" else {}))


def _finish_scan(a: App, pid: int, fmt, value, maps, addrs: list, net: int, t0: float, truncated: bool,
                 over_budget: bool, budget: int, **extra) -> dict:
    scan = {"pid": pid, "fmt": fmt, "addrs": addrs, "vals": [value] * len(addrs)}
    scan_id = max(a.scans, default=0) + 1
    a.scans[scan_id] = scan
    while len(a.scans) > MAX_SCANS:
        del a.scans[min(a.scans)]
    if truncated:
        extra["truncated"] = "a lista de candidatos foi cortada: restrinja regions ou use um valor mais raro"
    if over_budget:
        extra["budget"] = f"leitura parou em max_mb ({_size(budget)}): aumente max_mb ou restrinja regions"
    return _scan_result(a, scan_id, scan, maps, net, t0, **extra)


def _rescan(a: App, scan_id: int, condition: str) -> dict:
    scan = a.scans.get(scan_id)
    if scan is None:
        raise ToolError(f"scan_id {scan_id} não existe (o servidor guarda os {MAX_SCANS} últimos); rode scan_value")
    pid, fmt = scan["pid"], scan["fmt"]
    cond = condition.strip().lower()
    tests = {"changed": lambda new, old: new != old, "unchanged": lambda new, old: new == old,
             "increased": lambda new, old: new > old, "decreased": lambda new, old: new < old}
    if cond in tests:
        keep = tests[cond]
    else:
        target = _parse_value(fmt, condition)
        keep = lambda new, old: new == target  # noqa: E731
    maps = a.client.maps(pid, refresh=True)
    t0, scanned, addrs, vals = time.perf_counter(), 0, [], []
    pairs = sorted(zip(scan["addrs"], scan["vals"]))
    i = 0
    while i < len(pairs):
        m = find_map(maps, pairs[i][0])
        if m is None or not m.prot & PROT_READ:  # região sumiu: o candidato morre
            i += 1
            continue
        # junta os candidatos próximos numa leitura só, sem sair da região nem passar de 1 MiB
        start = end = pairs[i][0]
        j = i
        while j < len(pairs) and pairs[j][0] < m.end and pairs[j][0] - start < 1 << 20 and pairs[j][0] - end <= 4096:
            end = pairs[j][0]
            j += 1
        data = a.client.read(pid, start, end + fmt.size - start)
        scanned += len(data)
        for addr, old in pairs[i:j]:
            new = fmt.unpack_from(data, addr - start)[0]
            if keep(new, old):
                addrs.append(addr)
                vals.append(new)
        i = j
    scan["addrs"], scan["vals"] = addrs, vals
    return _scan_result(a, scan_id, scan, maps, scanned, t0)


@ps4_tool()
async def scan_value(pid: int, value: str, type: str = "u32", regions: str = "writable", max_mb: int = 512) -> str:
    """Procura um número (moedas, vida, munição) na memória, como o Cheat Engine. Guarda os candidatos
    no servidor e responde só a contagem e um scan_id. Depois mude o valor no jogo e chame rescan.
    type: u8/u16/u32/u64/i8/i16/i32/i64/f32/f64 (valor alinhado ao tamanho; float exato).
    regions: writable (padrão), all, code ou trecho do nome. Em recomps, prefira o heap guest."""
    a = get_app()
    fmt = _value_fmt(type)
    return await th(_scan_value, a, pid, fmt, _parse_value(fmt, value), regions, clamp(max_mb, 1, 4096) * 1024 * 1024)


@ps4_tool()
async def rescan(scan_id: int, condition: str) -> str:
    """Refina um scan_value relendo só os endereços candidatos (kilobytes, não a região inteira).
    condition: o valor novo (ex.: "38") ou increased, decreased, changed, unchanged em relação à
    passada anterior. Com até 20 candidatos, a resposta traz endereço e valor de cada um."""
    a = get_app()
    return await th(_rescan, a, scan_id, condition)


@ps4_tool()
async def read_text_region(pid: int, address: str, size: int = 256, encoding: str = "utf-16le") -> str:
    """Lê um bloco e decodifica como texto mostrando códigos de controle (<0A>, <001F>...) e bytes
    inválidos, para entender o formato das legendas. Inclui o hexdump dos primeiros 64 bytes."""
    a = get_app()
    maps = await th(a.client.maps, pid)
    addr = parse_address(address, maps)
    data = await th(a.client.read, pid, addr, clamp(size, 1, a.cfg.max_read))
    return {"address": hex(addr), "where": describe_address(maps, addr), "encoding": tx.normalize_encoding(encoding),
            "text": tx.decode_text(data, encoding), "hex_head": tx.hexdump(data[:64], addr)}


@ps4_tool(needs_ps4=False)
async def search_files(root: str, text: str, encoding: str = "auto", max_results: int = 50) -> str:
    """Procura a frase nos arquivos extraídos do jogo NO PC (pasta ou arquivo local), em UTF-8 e
    UTF-16LE por padrão. Use junto com search_memory para achar de onde vem uma legenda."""
    encs = None if encoding == "auto" else [tx.normalize_encoding(encoding)]
    root_path = str(Path(root).expanduser())
    if not Path(root_path).exists():
        raise ToolError(f"{root_path} não existe no PC")
    return {"matches": await th(tx.search_files, root_path, text, encs, clamp(max_results, 1, 500))}


@ps4_tool(needs_ps4=False)
async def compare_bytes(file_path: str, offset: str = "0", memory_hex: str = "", pid: int = 0,
                        address: str = "", size: int = 0) -> str:
    """Compara bytes da memória com um arquivo do PC no offset dado, para entender a transformação
    (XOR, UTF-16 vs UTF-8, deslocamento, byte-swap). Passe memory_hex OU pid+address+size."""
    a = get_app()
    if memory_hex:
        memory = bytes.fromhex(memory_hex.replace(" ", ""))
    elif pid and address and size:
        a.require_ps4()
        maps = await th(a.client.maps, pid)
        memory = await th(a.client.read, pid, parse_address(address, maps), clamp(size, 1, a.cfg.max_read))
    else:
        raise ToolError("passe memory_hex ou pid+address+size")
    return await th(tx.compare_bytes, memory, str(Path(file_path).expanduser()), parse_int(offset))


# --- fase 5 e 6 ---------------------------------------------------------------------------

@ps4_tool(needs_ps4=False)
async def mark_milestone(label: str, note: str = "") -> str:
    """Marca um marco da investigação no log de auditoria (ex.: "inicio", "arquivo_encontrado",
    "causa_encontrada", "hipotese_errada"). scripts/metrics.py usa isso no relatório da fase 5."""
    a = get_app()
    now = time.time()
    since_prev = round(now - a.last_milestone, 1) if a.last_milestone else None
    a.last_milestone = now
    a.audit.record("milestone", label, note=note, text=f"marco: {label} {note}".strip())
    return {"milestone": label, "since_server_start_s": round(now - a.started, 1), "since_previous_s": since_prev}


@ps4_tool(readonly=False)
async def notify_console(text: str) -> str:
    """Mostra uma notificação do sistema na tela do PS4 (aparece por cima do jogo). Use para avisar
    quem está no sofá: "breakpoint atingido", "pode continuar jogando"."""
    await th(get_app().client.notify, text[:200])
    return {"notified": text[:200]}


@ps4_tool(needs_ps4=False)
async def post_status(text: str) -> str:
    """Publica uma linha de status no app CCPS4 Agent Viewer do PS4 (requer PS4_BRIDGE=1)."""
    a = get_app()
    if not a.bridge:
        raise ToolError(a.bridge_error or "bridge desligado (PS4_BRIDGE=1 no .mcp.json)")
    a.audit.record("status", "post_status", text=text)
    return {"posted": True}


@ps4_tool(needs_ps4=False)
async def get_messages() -> str:
    """Mensagens enviadas pelo app no PS4 (controle/teclado), da mais antiga para a mais nova."""
    a = get_app()
    if not a.bridge:
        raise ToolError(a.bridge_error or "bridge desligado (PS4_BRIDGE=1 no .mcp.json)")
    return {"messages": a.bridge.pop_messages()}


if CFG.enable_deploy:

    @ps4_tool(readonly=False, needs_ps4=False)
    async def build_homebrew(project_dir: str) -> str:
        """Compila um homebrew com o toolchain OpenOrbis (make) e lista os .pkg gerados."""
        return await th(_build_homebrew, Path(project_dir).expanduser(), CFG.toolchain)

    @ps4_tool(readonly=False, destructive=True)
    async def install_pkg(pkg_path: str, ctx: Context = None) -> str:
        """Instala um .pkg no PS4 pelo Remote Package Installer (porta 12800, precisa estar aberto no
        console). O PC serve o arquivo por HTTP. Pede confirmação manual."""
        pkg = Path(pkg_path).expanduser()
        await confirm(ctx, f"Instalar {pkg.name} ({_size(pkg.stat().st_size)}) no PS4 {CFG.ps4_ip}?")
        return await th(_install_pkg, CFG.ps4_ip, pkg, CFG.rpi_port)


if CFG.enable_write:

    @ps4_tool(readonly=False, destructive=True)
    async def write_memory(pid: int, address: str, hex_bytes: str, ctx: Context = None) -> str:
        """Escreve bytes na memória do processo. Pode travar o jogo. Pede confirmação manual."""
        a = get_app()
        maps = await th(a.client.maps, pid)
        addr = parse_address(address, maps)
        data = bytes.fromhex(hex_bytes.replace(" ", ""))
        if not data or len(data) > 4096:
            raise ToolError("hex_bytes deve ter de 1 a 4096 bytes")
        before = await th(a.client.read, pid, addr, len(data))
        await confirm(ctx, f"Escrever {len(data)} bytes em {describe_address(maps, addr)} (pid {pid})?\nantes: {before.hex()}\ndepois: {data.hex()}")
        await th(a.client.write, pid, addr, data)
        return {"written": len(data), "address": hex(addr), "before": before.hex()}

    @ps4_tool(readonly=False, destructive=True)
    async def kill_process(ctx: Context = None) -> str:
        """Mata o processo anexado (SIGKILL). Pede confirmação manual."""
        a = _require_attached()
        await confirm(ctx, f"Matar o processo {a.debug.pid} ({a.attached_name})?")
        return await th(a.debug.kill)

    @ps4_tool(destructive=True)
    async def kernel_read(address: str, size: int = 256, ctx: Context = None) -> str:
        """Lê memória do kernel. Endereço inválido causa kernel panic. Pede confirmação manual."""
        a = get_app()
        addr = parse_int(address)
        size = clamp(size, 1, 4096)
        await confirm(ctx, f"Ler {size} bytes do KERNEL em {addr:#x}? Endereço inválido reinicia o console.")
        return tx.hexdump(await th(a.client.kernel_read, addr, size), addr)


def main() -> None:
    try:
        mcp.run()
    finally:
        if _app:
            _app.shutdown()


if __name__ == "__main__":
    main()
