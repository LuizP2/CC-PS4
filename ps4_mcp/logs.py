"""Leitor de logs do console em segundo plano.

Fontes aceitas (`source`):
- `tcp:HOST:PORTA`  — conecta no console e lê o stream (ex.: servidor de klog, porta 3232);
                      reconecta sozinho se cair; sem porta usa 3232;
- `udp:HOST:PORTA`  — escuta datagramas nesse endereço local (HOST 0.0.0.0 = todas as
                      interfaces); `udp:PORTA` também vale; cada datagrama traz linhas completas;
- `file:/caminho`   — segue um arquivo local como `tail -f`, começando pelos últimos 64 KiB;
                      trata truncamento e rotação;
- `off`             — desligado.

As linhas são decodificadas como UTF-8 (com substituição), perdem `\\r`, sequências ANSI e
outros caracteres de controle, e são cortadas em 4 KiB. O buffer guarda as últimas `maxlen`.
"""

from __future__ import annotations

import codecs
import os
import re
import socket
import threading
import time
from collections import deque

__all__ = ["LogReader"]

MAX_LINE = 4096
FILE_TAIL_BYTES = 64 * 1024
DEFAULT_TCP_PORT = 3232
_POLL = 0.25  # s: intervalo para conferir stop() e novidades no arquivo
_CONNECT_TIMEOUT = 5.0

_UNSET = object()
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # mantém só o \t


def _parse_source(source: str) -> tuple[str, object]:
    if source.lower() in ("", "off", "none"):
        return "off", None
    kind, _, rest = source.partition(":")
    kind = kind.lower()
    if kind == "file" and rest:
        return "file", os.path.expanduser(rest)
    if kind in ("tcp", "udp") and rest:
        host, sep, port = rest.rpartition(":")
        if not sep:  # tcp:HOST (porta padrão) ou udp:PORTA (todas as interfaces)
            host, port = (rest, str(DEFAULT_TCP_PORT)) if kind == "tcp" else ("", rest)
        host = host.strip("[]") or ("0.0.0.0" if kind == "udp" else "")
        if host and port.isdigit() and 0 < int(port) < 65536:
            return kind, (host, int(port))
    raise ValueError(
        f"Fonte de log inválida: {source!r}. Use 'tcp:HOST:PORTA' (klog, normalmente porta 3232), "
        "'udp:HOST:PORTA', 'file:/caminho' ou 'off'."
    )


def _keepalive(sock: socket.socket) -> None:
    """Detecta console desligado/sem rede em ~25 s em vez de ficar 'conectado' para sempre."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for name, value in (("TCP_KEEPIDLE", 10), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 3)):
        if hasattr(socket, name):
            try:
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
            except OSError:
                pass


class LogReader:
    """Lê logs do console numa thread daemon e guarda as últimas linhas (thread-safe)."""

    def __init__(self, source: str, maxlen: int = 5000, reconnect_delay: float = 3.0):
        self.source = (source or "off").strip()
        self.reconnect_delay = max(0.0, float(reconnect_delay))
        self._lines: deque[tuple[int, float, str]] = deque(maxlen=max(1, int(maxlen)))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0
        self._connected = False
        self._last_error: str | None = None
        self._last_line_at: float | None = None
        # montagem das linhas: só a thread leitora mexe nisto
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._partial = ""
        self._overflow = False  # descartando o resto de uma linha longa demais
        self._file_started = False
        try:
            self._kind, self._target = _parse_source(self.source)
        except ValueError as e:
            self._kind, self._target = "off", None
            self._last_error = str(e)

    # --- API pública -------------------------------------------------------------------

    def start(self) -> None:
        """Inicia a leitura em segundo plano (idempotente; com 'off' não faz nada)."""
        if self._kind == "off":
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name=f"LogReader[{self.source}]", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Para a leitura (as linhas já recebidas continuam disponíveis)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    def tail(self, n: int = 50, grep: str | None = None) -> list[str]:
        """Últimas `n` linhas (a mais nova por último), opcionalmente filtradas.

        `grep`: substring sem diferenciar maiúsculas; com prefixo `re:` é uma regex
        (também sem diferenciar maiúsculas), ex.: `re:(error|panic)`.
        """
        if n <= 0:
            return []
        match = _matcher(grep)
        with self._lock:
            entries = list(self._lines)
        out: list[str] = []
        for _, _, line in reversed(entries):
            if match is None or match(line):
                out.append(line)
                if len(out) >= n:
                    break
        out.reverse()
        return out

    def lines_since(self, seq: int) -> list[tuple[int, float, str]]:
        """Linhas com número de sequência maior que `seq`: (seq, hora epoch, linha)."""
        with self._lock:
            return [entry for entry in self._lines if entry[0] > seq]

    def status(self) -> dict:
        """Estado do leitor: fonte, conectado, linhas no buffer, total recebido, último erro."""
        with self._lock:
            return {
                "source": self.source,
                "connected": self._connected,
                "lines": len(self._lines),
                "total_received": self._seq,
                "last_error": self._last_error,
                "last_line_at": self._last_line_at,
            }

    # --- thread leitora -----------------------------------------------------------------

    def _run(self) -> None:
        runner = {"tcp": self._run_tcp, "udp": self._run_udp, "file": self._run_file}[self._kind]
        while not self._stop.is_set():
            try:
                runner()
            except Exception as e:  # a thread nunca morre: registra e tenta de novo
                self._set_state(error=self._explain(e))
            finally:
                self._set_state(connected=False)
                self._feed(b"", final=True)  # a linha incompleta vale como linha
            self._stop.wait(max(self.reconnect_delay, 0.05))

    def _run_tcp(self) -> None:
        host, port = self._target
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT) as sock:
            _keepalive(sock)
            sock.settimeout(_POLL)
            self._set_state(connected=True, error=None)
            while not self._stop.is_set():
                try:
                    data = sock.recv(65536)
                except TimeoutError:
                    continue
                if not data:
                    raise ConnectionError("o console encerrou a conexão de log")
                self._feed(data)

    def _run_udp(self) -> None:
        host, port = self._target
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.settimeout(_POLL)
            self._set_state(connected=True, error=None)
            while not self._stop.is_set():
                try:
                    data = sock.recv(65535)
                except TimeoutError:
                    continue
                self._feed(data, final=True)  # cada datagrama traz linhas completas

    def _run_file(self) -> None:
        path = self._target
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            if not self._file_started and st.st_size > FILE_TAIL_BYTES:
                f.seek(st.st_size - FILE_TAIL_BYTES - 1)
                if f.read(1) != b"\n":
                    f.readline()  # descarta a linha cortada no meio
            self._file_started = True  # reaberturas (rotação) leem o arquivo novo do início
            self._set_state(connected=True, error=None)
            while not self._stop.is_set():
                data = f.read(65536)
                if data:
                    self._feed(data)
                    continue
                try:
                    cur = os.stat(path)
                except FileNotFoundError:
                    return  # removido/rotacionado: o laço externo reabre
                if (cur.st_ino, cur.st_dev) != (st.st_ino, st.st_dev):
                    return  # rotacionado: arquivo novo no mesmo caminho
                if cur.st_size < f.tell():  # truncado: recomeça do início
                    self._feed(b"", final=True)
                    f.seek(0)
                    continue
                self._stop.wait(_POLL)

    # --- montagem das linhas ------------------------------------------------------------

    def _feed(self, data: bytes, final: bool = False) -> None:
        text = self._partial + self._decoder.decode(data, final)
        if final:
            self._decoder.reset()
        parts = text.split("\n")
        self._partial = parts.pop()
        for part in parts:
            if self._overflow:  # resto de uma linha longa demais (já registrada cortada)
                self._overflow = False
                continue
            self._add(part)
        if final:
            if self._partial and not self._overflow:
                self._add(self._partial)
            self._partial, self._overflow = "", False
        elif len(self._partial) > MAX_LINE:
            if not self._overflow:
                self._add(self._partial)
            self._partial, self._overflow = "", True

    def _add(self, raw: str) -> None:
        line = _CTRL_RE.sub("", _ANSI_RE.sub("", raw)).rstrip()
        if not line:
            return
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE] + " …[linha cortada]"
        now = time.time()
        with self._lock:
            self._seq += 1
            self._lines.append((self._seq, now, line))
            self._last_line_at = now

    def _set_state(self, connected=_UNSET, error=_UNSET) -> None:
        with self._lock:
            if connected is not _UNSET:
                self._connected = connected
            if error is not _UNSET:
                self._last_error = error

    def _explain(self, e: BaseException) -> str:
        if isinstance(e, ConnectionRefusedError):
            return (f"Conexão recusada ({self.source}). O servidor de log (klog, normalmente porta 3232) "
                    "está ativo no console?")
        if isinstance(e, TimeoutError):
            return f"Tempo esgotado ao conectar ({self.source}). Confira o IP do console e se ele está ligado."
        if isinstance(e, FileNotFoundError):
            return f"Arquivo de log não encontrado: {self._target}"
        return f"{type(e).__name__}: {e}"


def _matcher(grep: str | None):
    if not grep:
        return None
    if grep.startswith("re:"):
        try:
            rx = re.compile(grep[3:], re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"Regex inválida em grep ({e}): {grep[3:]!r}") from None
        return lambda line: rx.search(line) is not None
    needle = grep.casefold()
    return lambda line: needle in line.casefold()
