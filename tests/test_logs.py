import socket
import time

import pytest

from ps4_mcp.logs import LogReader


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def readers():
    created = []

    def make(*args, **kwargs):
        reader = LogReader(*args, **kwargs)
        created.append(reader)
        return reader

    yield make
    for reader in created:
        reader.stop()


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- TCP (klog) --------------------------------------------------------------------------


def test_tcp_fragments_disconnect_and_reconnect(readers):
    srv = socket.create_server(("127.0.0.1", 0))
    srv.settimeout(5)
    port = srv.getsockname()[1]
    reader = readers(f"tcp:127.0.0.1:{port}", reconnect_delay=0.05)
    reader.start()
    reader.start()  # idempotente

    conn, _ = srv.accept()
    with conn:
        for frag in (b"hel", b"lo\nwor", b"ld\r\n\x1b[1;31mERRO: ", b"falha\x1b[0m\n", b"sem quebra"):
            conn.sendall(frag)
            time.sleep(0.02)
        assert wait_for(lambda: reader.tail() == ["hello", "world", "ERRO: falha"])
    # o console fechou: a linha incompleta vale como linha e o leitor reconecta sozinho
    conn2, _ = srv.accept()
    srv.close()  # próximas reconexões serão recusadas
    with conn2:
        assert wait_for(lambda: reader.status()["connected"])
        data = "Olá, ação!\n".encode()
        conn2.sendall(data[:3])  # corta o 'á' (2 bytes em UTF-8) no meio
        time.sleep(0.05)
        conn2.sendall(data[3:])
        assert wait_for(lambda: reader.tail(1) == ["Olá, ação!"])
    assert reader.tail() == ["hello", "world", "ERRO: falha", "sem quebra", "Olá, ação!"]

    assert wait_for(lambda: "recusada" in (reader.status()["last_error"] or ""))
    st = reader.status()
    assert st["source"] == f"tcp:127.0.0.1:{port}" and not st["connected"]
    assert (st["lines"], st["total_received"]) == (5, 5)
    assert st["last_line_at"] is not None and st["last_line_at"] <= time.time()


def test_tcp_refused_then_console_comes_up(readers):
    port = free_port()
    reader = readers(f"tcp:127.0.0.1:{port}", reconnect_delay=0.05)
    reader.start()
    assert wait_for(lambda: reader.status()["last_error"] is not None)
    st = reader.status()
    assert "recusada" in st["last_error"] and "3232" in st["last_error"] and not st["connected"]

    with socket.create_server(("127.0.0.1", port)) as srv:
        srv.settimeout(5)
        conn, _ = srv.accept()
        with conn:
            conn.sendall(b"console de volta\n")
            assert wait_for(lambda: reader.tail() == ["console de volta"])
            st = reader.status()
            assert st["connected"] and st["last_error"] is None

    started = time.monotonic()
    reader.stop()
    assert time.monotonic() - started < 2
    assert not reader._thread.is_alive()


# --- UDP ---------------------------------------------------------------------------------


def test_udp_datagrams_with_several_lines(readers):
    port = free_port(socket.SOCK_DGRAM)
    reader = readers(f"udp:127.0.0.1:{port}")
    reader.start()
    assert wait_for(lambda: reader.status()["connected"])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(b"[klog] um\ndois\n", ("127.0.0.1", port))
        s.sendto("três sem quebra".encode(), ("127.0.0.1", port))
        s.sendto(b"\x1b[32mverde\x1b[0m\r\n\r\n", ("127.0.0.1", port))
        s.sendto(b"bin\xffario\n", ("127.0.0.1", port))
    assert wait_for(lambda: len(reader.tail()) == 5)
    assert reader.tail() == ["[klog] um", "dois", "três sem quebra", "verde", "bin�ario"]


# --- arquivo -----------------------------------------------------------------------------


def test_file_follow_append_truncate_rotate(tmp_path, readers):
    log = tmp_path / "game.log"
    log.write_bytes(b"antiga 1\nantiga 2\n")
    reader = readers(f"file:{log}", reconnect_delay=0.05)
    reader.start()
    assert wait_for(lambda: reader.tail() == ["antiga 1", "antiga 2"])
    assert reader.status()["connected"]

    with log.open("ab") as f:
        f.write(b"nova \x1b[33mamarela\x1b[0m\npela met")
    assert wait_for(lambda: reader.tail(1) == ["nova amarela"])
    with log.open("ab") as f:
        f.write(b"ade\n")
    assert wait_for(lambda: reader.tail(1) == ["pela metade"])

    log.write_bytes("recomeço\n".encode())  # truncado (ex.: '> game.log')
    assert wait_for(lambda: reader.tail(1) == ["recomeço"])

    log.rename(tmp_path / "game.log.1")  # rotação
    log.write_bytes(b"arquivo novo\n")
    assert wait_for(lambda: reader.tail(1) == ["arquivo novo"])
    assert reader.tail() == ["antiga 1", "antiga 2", "nova amarela", "pela metade", "recomeço", "arquivo novo"]


def test_file_starts_from_last_64k(tmp_path, readers):
    log = tmp_path / "big.log"
    lines = [f"linha {i:05d} " + "x" * 40 for i in range(3000)]  # 53 bytes por linha
    log.write_text("\n".join(lines) + "\n")
    reader = readers(f"file:{log}")
    reader.start()
    assert wait_for(lambda: reader.tail(1) == [lines[-1]])
    got = reader.tail(5000)
    assert got == lines[-len(got):]  # a primeira linha não veio cortada
    assert len(got) == 64 * 1024 // 53


def test_file_missing_then_created(tmp_path, readers):
    log = tmp_path / "later.log"
    reader = readers(f"file:{log}", reconnect_delay=0.05)
    reader.start()
    assert wait_for(lambda: "não encontrado" in (reader.status()["last_error"] or ""))
    log.write_bytes(b"apareceu\n")
    assert wait_for(lambda: reader.tail() == ["apareceu"])


def test_tail_grep_regex_and_lines_since(tmp_path, readers):
    log = tmp_path / "game.log"
    log.write_text(
        "boot ok\n[GAME] Loading subtitles\nERROR: font missing glyph ç\nwarn: slow frame\n[game] subtitle id=42\n"
    )
    reader = readers(f"file:{log}", maxlen=100)
    reader.start()
    assert wait_for(lambda: len(reader.tail()) == 5)
    assert reader.tail(grep="game") == ["[GAME] Loading subtitles", "[game] subtitle id=42"]
    assert reader.tail(1, grep="GAME") == ["[game] subtitle id=42"]
    assert reader.tail(grep="GLYPH Ç") == ["ERROR: font missing glyph ç"]
    assert reader.tail(grep="re:^(error|warn)") == ["ERROR: font missing glyph ç", "warn: slow frame"]
    assert reader.tail(grep=r"re:id=\d+$") == ["[game] subtitle id=42"]
    assert reader.tail(grep="nada") == []
    assert reader.tail(0) == []
    with pytest.raises(ValueError, match="Regex inválida"):
        reader.tail(grep="re:(")

    since = reader.lines_since(3)
    assert [(seq, line) for seq, _, line in since] == [(4, "warn: slow frame"), (5, "[game] subtitle id=42")]
    assert isinstance(since[0][1], float)
    assert reader.lines_since(5) == []
    assert len(reader.lines_since(0)) == 5


def test_maxlen_and_long_lines(tmp_path, readers):
    log = tmp_path / "spam.log"
    log.write_bytes(b"".join(b"l%d\n" % i for i in range(10)) + b"A" * 10000 + b"\nfim\n")
    reader = readers(f"file:{log}", maxlen=3, reconnect_delay=0.05)
    reader.start()
    assert wait_for(lambda: reader.tail(1) == ["fim"])
    tail = reader.tail()
    assert tail[0] == "l9" and tail[2] == "fim"
    assert tail[1].startswith("A" * 4096) and "linha cortada" in tail[1] and len(tail[1]) < 4200
    st = reader.status()
    assert (st["lines"], st["total_received"]) == (3, 12)

    # linha longa chegando aos poucos, sem quebra: registra cortada e descarta o resto
    with log.open("ab") as f:
        f.write(b"B" * 6000)
    assert wait_for(lambda: reader.tail(1)[0].startswith("BBBB"))
    with log.open("ab") as f:
        f.write(b"B" * 3000 + b" resto\nok\n")
    assert wait_for(lambda: reader.tail(1) == ["ok"])
    assert reader.tail(2)[0].startswith("B" * 4096) and "resto" not in reader.tail(2)[0]


def test_off_and_invalid_sources():
    off = LogReader("off")
    off.start()
    assert off.status() == {"source": "off", "connected": False, "lines": 0, "total_received": 0,
                            "last_error": None, "last_line_at": None}
    assert off.tail() == [] and off.lines_since(0) == []
    off.stop()

    for bad in ("tcp:", "serial:/dev/ttyUSB0", "udp:host:porta", "tcp:1.2.3.4:99999"):
        reader = LogReader(bad)
        reader.start()
        st = reader.status()
        assert "inválida" in st["last_error"] and not st["connected"], bad
