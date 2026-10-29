"""Cliente FTP para o servidor do console (GoldHEN, baseado no ftps4 do xerpi).

Particularidades tratadas:
- porta 2121, login anônimo, modo passivo, modo binário; o IP anunciado na resposta do PASV
  é ignorado (usa-se o IP do console), porque o ftps4 pode anunciar um endereço errado;
- só LIST (MLSD normalmente não existe). A listagem é feita com CWD + LIST sem argumento,
  como o FileZilla faz: o ftps4 responde a "LIST caminho_inexistente" listando a pasta atual
  em silêncio, enquanto o CWD devolve um 550 claro;
- SIZE pode não existir: cai para a listagem da pasta-mãe;
- o ftps4 não lê o canal de controle durante um RETR (ABOR não adianta). Para ler só o começo
  de um arquivo, a conexão de dados é fechada; se a resposta final (226/426) não chegar logo,
  a conexão de controle é descartada e refeita na próxima operação.
"""

from __future__ import annotations

import contextlib
import errno
import ftplib
import hashlib
import os
import posixpath
import re
import socket
import tempfile
import threading

__all__ = ["FTPError", "ConsoleFTP", "parse_list_line"]

_BLOCK = 64 * 1024
# erros "de conexão": derrubam a conexão de controle e valem uma nova tentativa
_CONNECTION_ERRORS = (EOFError, OSError, ftplib.Error)


class FTPError(Exception):
    """Falha numa operação FTP com o console; a mensagem (pt-BR) já explica o que houve."""


_LIST_RE = re.compile(
    r"^(?P<perms>[-bcdlps][-rwxsStTlL]{9})[.+@]?\s+"
    r"(?:\S+\s+){0,4}?"  # nº de links, dono, grupo (alguns servidores omitem campos)
    r"(?P<size>\d+)\s+"
    r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<when>\d{1,2}:\d{2}(?::\d{2})?|\d{4})\s+"
    r"(?P<name>.+)$",
    re.IGNORECASE,
)


def parse_list_line(line: str) -> dict | None:
    """Interpreta uma linha de LIST estilo Unix (formato do ftps4/GoldHEN, pyftpdlib, ls -l).

    Ex.: `drwxr-xr-x 1 ps4 ps4 512 Jan 01 00:00 app0` →
    `{"name": "app0", "type": "dir", "size": 512, "perms": "drwxr-xr-x", "mtime": "Jan 01 00:00"}`.
    Links simbólicos têm type "link" e a chave extra "target". Devolve None para `.`, `..`,
    `total N` e linhas que não seguem o formato.
    """
    m = _LIST_RE.match(line.strip("\r\n"))
    if not m:
        return None
    perms = m["perms"]
    kind = {"d": "dir", "l": "link"}.get(perms[0].lower(), "file")
    name = m["name"].rstrip()
    target = None
    if kind == "link" and " -> " in name:
        name, target = name.split(" -> ", 1)
    if name in (".", ".."):
        return None
    entry = {
        "name": name,
        "type": kind,
        "size": int(m["size"]),
        "perms": perms,
        "mtime": f"{m['month'].title()} {int(m['day']):02d} {m['when']}",
    }
    if target is not None:
        entry["target"] = target
    return entry


def _clean_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise FTPError("Caminho vazio: informe um caminho absoluto do console (ex.: /user/app).")
    if any(c in path for c in "\r\n\0"):
        raise FTPError(f"Caminho inválido (contém quebra de linha ou NUL): {path!r}")
    normalized = posixpath.normpath("/" + path)
    return "/" + normalized.lstrip("/")


def _reply(e: BaseException) -> str:
    return str(e).strip() or type(e).__name__


def _explain(e: BaseException) -> str:
    if isinstance(e, TimeoutError):
        return "tempo esgotado esperando o console"
    if isinstance(e, EOFError):
        return "o servidor FTP fechou a conexão"
    if isinstance(e, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return f"conexão interrompida pelo console ({e})"
    if isinstance(e, ftplib.Error):
        return f"resposta inesperada do servidor: {_reply(e)}"
    return f"{type(e).__name__}: {e}"


class ConsoleFTP:
    """Cliente FTP do console: thread-safe (um lock), conexão preguiçosa e uma reconexão
    automática (com nova tentativa) em erros de conexão (EOF, socket, respostas 4xx)."""

    abort_wait = 2.0  # segundos esperando a resposta final depois de cortar um RETR

    def __init__(self, host: str, port: int = 2121, user: str = "anonymous", password: str = "",
                 timeout: float = 15.0):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.timeout = timeout
        self._ftp: ftplib.FTP | None = None
        self._lock = threading.Lock()

    # --- API pública -------------------------------------------------------------------

    def list_dir(self, path: str) -> list[dict]:
        """Lista uma pasta do console: pastas primeiro, depois por nome (ver parse_list_line)."""
        path = _clean_path(path)
        return self._call(f"listar {path}", lambda ftp: self._list(ftp, path))

    def read_file(self, path: str, max_bytes: int) -> tuple[bytes, bool]:
        """Lê no máximo `max_bytes` do início do arquivo. Devolve (dados, truncado)."""
        path = _clean_path(path)
        limit = max(0, int(max_bytes))

        def op(ftp: ftplib.FTP) -> tuple[bytes, bool]:
            chunks: list[bytes] = []
            _, truncated = self._retr(ftp, path, limit, chunks.append)
            return b"".join(chunks), truncated

        return self._call(f"ler {path}", op)

    def download(self, path: str, dest_path: str, max_bytes: int | None = None) -> dict:
        """Baixa um arquivo para o PC (grava num temporário e renomeia no fim).

        Se `dest_path` for uma pasta existente, o arquivo vai para dentro dela com o mesmo nome.
        Devolve {"local_path", "size", "sha256", "truncated"}.
        """
        path = _clean_path(path)
        dest = os.path.abspath(os.path.expanduser(dest_path))
        if os.path.isdir(dest):
            dest = os.path.join(dest, posixpath.basename(path) or "download.bin")
        folder = os.path.dirname(dest)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise FTPError(f"Não foi possível criar a pasta de destino {folder}: {e}") from e
        limit = None if max_bytes is None else max(0, int(max_bytes))

        def op(ftp: ftplib.FTP) -> tuple[str, int, str, bool]:
            digest = hashlib.sha256()
            try:
                out = tempfile.NamedTemporaryFile(dir=folder, prefix=".ps4ftp-", suffix=".part", delete=False)
            except OSError as e:
                raise FTPError(f"Não foi possível criar arquivo temporário em {folder}: {e}") from e

            def sink(chunk: bytes) -> None:
                try:
                    out.write(chunk)
                except OSError as e:
                    raise FTPError(f"Erro ao gravar no PC ({dest}): {e}") from e
                digest.update(chunk)

            try:
                with out:
                    size, truncated = self._retr(ftp, path, limit, sink)
                    try:
                        out.flush()
                    except OSError as e:
                        raise FTPError(f"Erro ao gravar no PC ({dest}): {e}") from e
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(out.name)
                raise
            return out.name, size, digest.hexdigest(), truncated

        tmp, size, sha256, truncated = self._call(f"baixar {path}", op)
        try:
            os.replace(tmp, dest)
        except OSError as e:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise FTPError(f"Não foi possível salvar {dest}: {e}") from e
        return {"local_path": dest, "size": size, "sha256": sha256, "truncated": truncated}

    def size(self, path: str) -> int | None:
        """Tamanho do arquivo em bytes. Usa SIZE; se o servidor não suportar, procura o arquivo
        na listagem da pasta-mãe. None para pastas/links; FTPError se não existir."""
        path = _clean_path(path)

        def op(ftp: ftplib.FTP) -> int | None:
            try:
                resp = ftp.sendcmd("SIZE " + path)
            except ftplib.error_perm:  # 500/502 (não suportado) ou 550 (pasta, inexistente)
                resp = ""
            m = re.match(r"213\s+(\d+)", resp)
            if m:
                return int(m.group(1))
            parent, name = posixpath.split(path)
            if not name:
                return None  # "/"
            for entry in self._list(ftp, parent):
                if entry["name"] == name:
                    return entry["size"] if entry["type"] == "file" else None
            raise FTPError(f"Arquivo não encontrado no console: {path}")

        return self._call(f"obter o tamanho de {path}", op)

    def close(self) -> None:
        """Encerra a conexão (QUIT). O cliente continua utilizável: reconecta sob demanda."""
        with self._lock:
            ftp, self._ftp = self._ftp, None
            if ftp is None:
                return
            try:
                if ftp.sock is not None:
                    ftp.sock.settimeout(2.0)
                ftp.quit()
            except Exception:
                pass
            finally:
                ftp.close()

    # --- internos ------------------------------------------------------------------------

    def _call(self, what: str, op):
        with self._lock:
            for attempt in range(2):
                ftp = self._connection()
                try:
                    return op(ftp)
                except FTPError:
                    raise
                except ftplib.error_perm as e:
                    raise FTPError(f"O servidor FTP do console recusou ao {what}: {_reply(e)}") from e
                except UnicodeError as e:
                    self._drop()
                    raise FTPError(f"Resposta do console com codificação inválida ao {what}: {e}") from e
                except _CONNECTION_ERRORS as e:
                    self._drop()
                    if attempt:
                        raise FTPError(
                            f"Falha de comunicação com o FTP do console ({self.host}:{self.port}) ao "
                            f"{what}: {_explain(e)}. Já tentei reconectar uma vez; confira se o PS4 "
                            "está ligado e se o servidor FTP do GoldHEN continua ativo."
                        ) from e
        raise AssertionError("inalcançável")

    def _connection(self) -> ftplib.FTP:
        if self._ftp is not None:
            return self._ftp
        ftp = ftplib.FTP(timeout=self.timeout, encoding="utf-8")
        try:
            ftp.connect(self.host, self.port)
            ftp.login(self.user, self.password)
            ftp.set_pasv(True)
            ftp.voidcmd("TYPE I")
        except ftplib.error_perm as e:
            ftp.close()
            raise FTPError(
                f"O FTP do console ({self.host}:{self.port}) recusou o login do usuário "
                f"{self.user!r}: {_reply(e)}. O FTP do GoldHEN aceita login anônimo."
            ) from e
        except (*_CONNECTION_ERRORS, UnicodeError) as e:
            ftp.close()
            raise FTPError(self._connect_hint(e)) from e
        self._ftp = ftp
        return ftp

    def _connect_hint(self, e: BaseException) -> str:
        where = f"{self.host}:{self.port}"
        if isinstance(e, ConnectionRefusedError):
            return (
                f"Conexão recusada em {where}: não há servidor FTP nessa porta. Ative o servidor FTP "
                "no menu do GoldHEN e confira a porta (normalmente 2121)."
            )
        if isinstance(e, TimeoutError):
            return (
                f"Tempo esgotado ({self.timeout:g}s) ao conectar em {where}. Confira o IP do console, "
                "se o PS4 está ligado (fora do modo de repouso) e na mesma rede, e se o servidor FTP "
                "do GoldHEN está ativo (porta 2121)."
            )
        if isinstance(e, socket.gaierror):
            return f"Endereço do console inválido ou não resolvido: {self.host!r} ({e})."
        if isinstance(e, OSError) and e.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            return f"Console inalcançável em {where} ({e}). Confira o IP e a rede."
        return f"Falha ao conectar no FTP do console em {where}: {_explain(e)}."

    def _drop(self) -> None:
        ftp, self._ftp = self._ftp, None
        if ftp is not None:
            with contextlib.suppress(Exception):
                ftp.close()

    def _list(self, ftp: ftplib.FTP, path: str) -> list[dict]:
        try:
            ftp.cwd(path)
        except ftplib.error_perm as e:
            raise FTPError(
                f"Pasta não encontrada no console (ou não é uma pasta): {path}. "
                f"Resposta do servidor: {_reply(e)}"
            ) from e
        raw: list[bytes] = []
        ftp.retrbinary("LIST", raw.append)
        entries = [e for line in b"".join(raw).decode("utf-8", "replace").splitlines()
                   if (e := parse_list_line(line))]
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower(), e["name"]))
        return entries

    def _retr(self, ftp: ftplib.FTP, path: str, limit: int | None, sink) -> tuple[int, bool]:
        """Transfere o arquivo para `sink` (até `limit` bytes). Devolve (bytes, truncado)."""
        ftp.voidcmd("TYPE I")
        try:
            conn = ftp.transfercmd("RETR " + path)
        except ftplib.error_perm as e:
            raise FTPError(
                f"Não foi possível ler {path} no console: {_reply(e)}. Confira se o caminho existe "
                "e é um arquivo (use list_dir na pasta)."
            ) from e
        total, truncated = 0, False
        try:
            with conn:
                while True:
                    # lê 1 byte além do limite para saber se o arquivo continua
                    want = _BLOCK if limit is None else min(_BLOCK, limit + 1 - total)
                    chunk = conn.recv(want)
                    if not chunk:
                        break
                    if limit is not None and total + len(chunk) > limit:
                        chunk, truncated = chunk[: limit - total], True
                    if chunk:
                        sink(chunk)
                        total += len(chunk)
                    if truncated:
                        break
        except BaseException:
            self._drop()  # estado do canal de controle desconhecido
            raise
        if truncated:
            self._finish_aborted(ftp)
        else:
            ftp.voidresp()
        return total, truncated

    def _finish_aborted(self, ftp: ftplib.FTP) -> None:
        """Depois de fechar a conexão de dados no meio do RETR, espera a resposta final por
        pouco tempo. Se ela não vier (o ftps4 continua lendo o arquivo até o fim antes de
        responder) ou vier algo estranho, descarta a conexão de controle."""
        try:
            ftp.sock.settimeout(min(self.abort_wait, self.timeout))
            resp = ftp.getmultiline()
            ftp.sock.settimeout(self.timeout)
            if resp[:1] in ("2", "4", "5"):
                return
        except Exception:
            pass
        self._drop()
