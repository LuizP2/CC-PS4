import contextlib
import hashlib
import logging
import os
import posixpath
import random
import socket
import socketserver
import threading
import time
from pathlib import Path

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from ps4_mcp.ftp import ConsoleFTP, FTPError, parse_list_line

logging.getLogger("pyftpdlib").setLevel(logging.WARNING)

BIG = random.Random(42).randbytes(300_000)

# --- parse_list_line --------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        # formato do ftps4/GoldHEN: "%s 1 ps4 ps4 %llu %s %2d %s %s"
        ("drwxr-xr-x 1 ps4 ps4 512 Jan 01 00:00 app0",
         {"name": "app0", "type": "dir", "size": 512, "perms": "drwxr-xr-x", "mtime": "Jan 01 00:00"}),
        ("-rw-rw-rw- 1 root root 123456 Mar  7  2019 eboot.bin",
         {"name": "eboot.bin", "type": "file", "size": 123456, "perms": "-rw-rw-rw-", "mtime": "Mar 07 2019"}),
        ("-rw-rw-rw- 1 ps4 ps4 4194304 Oct  9 2019 sce_sys param.sfo copy\r\n",
         {"name": "sce_sys param.sfo copy", "type": "file", "size": 4194304, "perms": "-rw-rw-rw-",
          "mtime": "Oct 09 2019"}),
        ("drwxrwxrwx 1 ps4 ps4 0 Dec 31 23:59 CUSA12025\r",
         {"name": "CUSA12025", "type": "dir", "size": 0, "perms": "drwxrwxrwx", "mtime": "Dec 31 23:59"}),
        ("lrwxr-xr-x 1 root root 9 Jan  1 2020 data -> /user/data",
         {"name": "data", "type": "link", "size": 9, "perms": "lrwxr-xr-x", "mtime": "Jan 01 2020",
          "target": "/user/data"}),
        ("-rw-r--r-- 1 ps4 ps4 100 Jan  1 2019 2020 notas.txt",
         {"name": "2020 notas.txt", "type": "file", "size": 100, "perms": "-rw-r--r--", "mtime": "Jan 01 2019"}),
        # pyftpdlib / ls -l
        ("-rw-r--r--   1 owner    group        7045120 Sep 02  3:47 music.mp3",
         {"name": "music.mp3", "type": "file", "size": 7045120, "perms": "-rw-r--r--", "mtime": "Sep 02 3:47"}),
        ("drwxr-xr-x    2 0        0            4096 Feb 29 12:00 numeric_owner",
         {"name": "numeric_owner", "type": "dir", "size": 4096, "perms": "drwxr-xr-x", "mtime": "Feb 29 12:00"}),
        ("drwxr-xr-x 1 ps4 512 Jan 01 00:00 sem_grupo",
         {"name": "sem_grupo", "type": "dir", "size": 512, "perms": "drwxr-xr-x", "mtime": "Jan 01 00:00"}),
    ],
)
def test_parse_list_line(line, expected):
    assert parse_list_line(line) == expected


@pytest.mark.parametrize(
    "line",
    ["drwxr-xr-x 1 ps4 ps4 512 Jan 01 00:00 .", "drwxr-xr-x 1 ps4 ps4 512 Jan 01 00:00 ..",
     "total 12", "", "226 Transfer complete.", "garbage line here", "03-07-19  12:00PM  <DIR>  dos"],
)
def test_parse_list_line_ignored(line):
    assert parse_list_line(line) is None


# --- árvore de arquivos do "console" ----------------------------------------------------


@pytest.fixture
def console_root(tmp_path):
    root = tmp_path / "console"
    game = root / "user" / "app" / "CUSA12345"
    game.mkdir(parents=True)
    (root / "system").mkdir()
    (game / "app.pkg").write_bytes(BIG)
    (root / "readme.txt").write_bytes("olá\n".encode())
    (root / "my save.dat").write_bytes(b"x" * 10)
    old = time.mktime((2019, 3, 7, 12, 0, 0, 0, 0, -1))
    os.utime(root / "readme.txt", (old, old))
    return root


class PyFtpd:
    def __init__(self, root: Path):
        auth = DummyAuthorizer()
        auth.add_anonymous(str(root))
        handler = type("Handler", (FTPHandler,), {"authorizer": auth})
        self.server = FTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.socket.getsockname()[1]
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stopping.is_set():
            self.server.serve_forever(timeout=0.02, blocking=False, handle_exit=False)

    def stop(self):
        self._stopping.set()
        self._thread.join(5)
        self.server.close_all()


@pytest.fixture
def client(console_root):
    server = PyFtpd(console_root)
    ftp = ConsoleFTP("127.0.0.1", server.port, timeout=5)
    yield ftp
    ftp.close()
    server.stop()


# --- pyftpdlib --------------------------------------------------------------------------


def test_list_dir_root_sorted(client):
    entries = client.list_dir("/")
    assert [(e["name"], e["type"]) for e in entries] == [
        ("system", "dir"), ("user", "dir"), ("my save.dat", "file"), ("readme.txt", "file"),
    ]
    readme = entries[-1]
    assert readme["size"] == 5 and readme["mtime"] == "Mar 07 2019"


def test_list_dir_normalizes_paths(client):
    entries = client.list_dir("user/app/../app/CUSA12345/")
    assert [(e["name"], e["size"]) for e in entries] == [("app.pkg", len(BIG))]


def test_read_file_truncation_then_more_operations(client):
    data, truncated = client.read_file("/user/app/CUSA12345/app.pkg", 1000)
    assert (data, truncated) == (BIG[:1000], True)
    assert client.read_file("/readme.txt", 1000) == ("olá\n".encode(), False)
    assert len(client.list_dir("/")) == 4
    assert client.read_file("/readme.txt", 5) == ("olá\n".encode(), False)  # tamanho exato
    assert client.read_file("/readme.txt", 4) == ("olá\n".encode()[:4], True)
    assert client.read_file("/readme.txt", 0) == (b"", True)
    data, truncated = client.read_file("/user/app/CUSA12345/app.pkg", 10**9)
    assert (data, truncated) == (BIG, False)


def test_download_sha256(client, tmp_path):
    out = tmp_path / "pc" / "dumps"
    info = client.download("/user/app/CUSA12345/app.pkg", str(out / "app.pkg"))
    assert info == {"local_path": str(out / "app.pkg"), "size": len(BIG),
                    "sha256": hashlib.sha256(BIG).hexdigest(), "truncated": False}
    assert (out / "app.pkg").read_bytes() == BIG

    part = client.download("/user/app/CUSA12345/app.pkg", str(out), max_bytes=1234)  # pasta existente
    assert part["local_path"] == str(out / "app.pkg")
    assert (part["size"], part["truncated"]) == (1234, True)
    assert part["sha256"] == hashlib.sha256(BIG[:1234]).hexdigest()
    assert sorted(os.listdir(out)) == ["app.pkg"]  # nada de .part sobrando


def test_size(client):
    assert client.size("/user/app/CUSA12345/app.pkg") == len(BIG)
    assert client.size("/user") is None
    with pytest.raises(FTPError, match="não encontrado"):
        client.size("/nada.bin")


def test_nonexistent_paths_raise_clear_errors(client, tmp_path):
    with pytest.raises(FTPError, match="Não foi possível ler /nada.bin"):
        client.read_file("/nada.bin", 100)
    with pytest.raises(FTPError, match="Pasta não encontrada.*/nada"):
        client.list_dir("/nada")
    with pytest.raises(FTPError, match="Pasta não encontrada"):
        client.list_dir("/readme.txt")  # arquivo, não pasta
    dest = tmp_path / "dl"
    with pytest.raises(FTPError):
        client.download("/user/nada.pkg", str(dest / "x.pkg"))
    assert os.listdir(dest) == []
    with pytest.raises(FTPError, match="inválido"):
        client.list_dir("/x\r\nDELE readme.txt")
    assert len(client.list_dir("/")) == 4  # o cliente continua utilizável


def test_concurrent_calls_are_serialized(client):
    errors, results = [], []

    def worker():
        try:
            for _ in range(3):
                results.append(client.read_file("/user/app/CUSA12345/app.pkg", 5000))
        except Exception as e:  # pragma: no cover - só aparece se falhar
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
    assert results == [(BIG[:5000], True)] * 12


def test_connection_refused_message():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ftp = ConsoleFTP("127.0.0.1", port, timeout=2)
    with pytest.raises(FTPError, match="recusada.*GoldHEN.*2121"):
        ftp.list_dir("/")


# --- servidor que imita o ftps4/GoldHEN -------------------------------------------------

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def ftps4_line(name: str, path: Path) -> str:
    st = path.stat()
    t = time.localtime(st.st_mtime)
    when = f"{t.tm_hour:02d}:{t.tm_min:02d}" if t.tm_year == time.localtime().tm_year else f"{t.tm_year:04d}"
    perms = "drwxr-xr-x" if path.is_dir() else "-rw-rw-rw-"
    return f"{perms} 1 ps4 ps4 {st.st_size} {MONTHS[t.tm_mon - 1]} {t.tm_mday:2d} {when} {name}\r\n"


class Ftps4Handler(socketserver.StreamRequestHandler):
    """Comportamento do ftps4: sem SIZE/MLSD, LIST de caminho inexistente lista a pasta atual,
    IP falso no PASV e, no RETR, ignora erros de envio e só responde depois de ler o arquivo."""

    def handle(self):
        self.server.control_sockets.append(self.connection)
        self.cwd, self.pasv = "/", None
        self.send("220 FTPS4 Server ready.")
        while True:
            try:
                line = self.rfile.readline()
            except OSError:
                return
            if not line:
                return
            cmd, _, arg = line.decode("utf-8", "replace").rstrip("\r\n").partition(" ")
            self.server.commands.append((cmd.upper(), arg))
            method = getattr(self, "do_" + cmd.upper(), None)
            if method is None:
                self.send("502 Sorry, command not implemented. :(")
            elif method(arg) is False:
                return

    def send(self, msg):
        with contextlib.suppress(OSError):
            self.wfile.write((msg + "\r\n").encode())

    def local(self, arg) -> Path:
        return self.server.root / posixpath.normpath(posixpath.join(self.cwd, arg)).lstrip("/")

    def accept_data(self):
        self.pasv.settimeout(5)
        conn, _ = self.pasv.accept()
        self.pasv.close()
        return conn

    def do_USER(self, arg):
        self.send("331 Username OK, need password b0ss.")

    def do_PASS(self, arg):
        self.send("230 User logged in!")

    def do_TYPE(self, arg):
        self.send("200 Switching to Binary mode.")

    def do_QUIT(self, arg):
        self.send("221 Goodbye senpai :'(")
        return False

    def do_PASV(self, arg):
        self.pasv = socket.create_server(("127.0.0.1", 0))
        port = self.pasv.getsockname()[1]
        self.send(f"227 Entering Passive Mode (10,0,0,99,{port >> 8},{port & 0xFF})")

    def do_CWD(self, arg):
        if self.local(arg).is_dir():
            self.cwd = posixpath.normpath(posixpath.join(self.cwd, arg))
            self.send("250 Requested file action okay, completed.")
        else:
            self.send("550 Invalid directory.")

    def do_LIST(self, arg):
        target = self.local(arg) if arg and self.local(arg).exists() else self.local("")
        conn = self.accept_data()
        self.send("150 Opening ASCII mode data transfer for LIST.")
        lines = [ftps4_line(".", target), ftps4_line("..", target.parent)]
        lines += [ftps4_line(p.name, p) for p in target.iterdir()]
        conn.sendall("".join(lines).encode())
        conn.close()
        self.send("226 Transfer complete.")

    def do_RETR(self, arg):
        target = self.local(arg)
        if not target.is_file():
            self.send("550 File not found.")
            return
        conn = self.accept_data()
        self.send("150 Opening Image mode data transfer.")
        with target.open("rb") as f:
            while chunk := f.read(8192):
                with contextlib.suppress(OSError):
                    conn.sendall(chunk)
        conn.close()
        time.sleep(self.server.final_reply_delay)  # arquivo grande: o console ainda está lendo
        self.send("226 Transfer completed.")


class FakeFtps4(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root: Path):
        super().__init__(("127.0.0.1", 0), Ftps4Handler)
        self.root = root
        self.final_reply_delay = 0.0
        self.commands: list[tuple[str, str]] = []
        self.control_sockets: list[socket.socket] = []
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def logins(self) -> int:
        return sum(1 for cmd, _ in self.commands if cmd == "USER")

    def kick_all(self):
        for sock in self.control_sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    def stop(self):
        self.shutdown()
        self.server_close()
        self.kick_all()


@pytest.fixture
def ftps4(console_root):
    server = FakeFtps4(console_root)
    ftp = ConsoleFTP("127.0.0.1", server.port, timeout=5)
    yield server, ftp
    ftp.close()
    server.stop()


def test_ftps4_listing_and_size_fallback(ftps4):
    server, ftp = ftps4
    entries = ftp.list_dir("/")
    assert [(e["name"], e["type"]) for e in entries] == [
        ("system", "dir"), ("user", "dir"), ("my save.dat", "file"), ("readme.txt", "file"),
    ]
    assert entries[-1]["mtime"] == "Mar 07 2019"
    assert ftp.size("/user/app/CUSA12345/app.pkg") == len(BIG)  # SIZE → 502 → LIST da pasta-mãe
    assert ("SIZE", "/user/app/CUSA12345/app.pkg") in server.commands
    assert not any(cmd == "MLSD" for cmd, _ in server.commands)
    # o ftps4 listaria a pasta atual para um caminho inexistente; o CWD evita esse engano
    with pytest.raises(FTPError, match="Pasta não encontrada"):
        ftp.list_dir("/nada")


def test_ftps4_truncated_read_reconnects_when_server_stays_busy(ftps4):
    server, ftp = ftps4
    ftp.abort_wait = 0.2
    server.final_reply_delay = 1.0
    started = time.monotonic()
    assert ftp.read_file("/user/app/CUSA12345/app.pkg", 100) == (BIG[:100], True)
    assert time.monotonic() - started < 0.9  # não esperou o servidor terminar de ler
    server.final_reply_delay = 0.0
    assert len(ftp.list_dir("/")) == 4
    assert server.logins() == 2  # conexão de controle descartada e refeita
    assert ftp.download("/readme.txt", str(server.root.parent / "r.txt"))["size"] == 5


def test_ftps4_full_read_and_missing_file(ftps4):
    server, ftp = ftps4
    assert ftp.read_file("/user/app/CUSA12345/app.pkg", len(BIG)) == (BIG, False)
    with pytest.raises(FTPError, match="550 File not found"):
        ftp.read_file("/nada.bin", 10)
    assert ftp.read_file("/readme.txt", 100) == ("olá\n".encode(), False)
    assert server.logins() == 1


def test_ftps4_reconnects_after_server_drops_connection(ftps4):
    server, ftp = ftps4
    assert len(ftp.list_dir("/")) == 4
    server.kick_all()  # ex.: o FTP do GoldHEN reiniciou
    assert len(ftp.list_dir("/user")) == 1
    assert server.logins() == 2
