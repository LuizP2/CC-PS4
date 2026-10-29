"""Bridge da fase 6 (protocolo v1 do app no PS4) e instalação de PKG via Remote Package Installer."""

import hashlib
import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ps4_mcp.bridge import Bridge
from ps4_mcp.deploy import DeployError, _lenient_json, install_pkg


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def bridge():
    udp_port = _free_udp_port()
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", udp_port))
    listener.settimeout(3)
    b = Bridge(port=0, discovery_port=udp_port, allowed_ip="127.0.0.1", host="127.0.0.1",
               state_fn=lambda: {"agent": "idle", "attached_pid": 77, "process": None}, broadcast_interval=0.2).start()
    yield b, listener
    b.stop()
    listener.close()


def _get(b: Bridge, path: str) -> tuple[int, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{b.port}{path}", timeout=3) as r:
        return r.status, r.read().decode()


def test_bridge_discovery_announce(bridge):
    b, listener = bridge
    data, _ = listener.recvfrom(100)
    assert data == f"CCPS4-BRIDGE v1 http={b.port}\n".encode()


def test_bridge_feed_state_and_messages(bridge):
    b, _ = bridge
    for i in range(150):
        b.add("tool", f"read_memory\tpid=1\nlinha {i} çãé")
    status, body = _get(b, "/v1/feed?since=0")
    lines = body.splitlines()
    assert status == 200 and len(lines) == 100
    seq, ts, kind, text = lines[-1].split("\t")
    assert seq == "150" and kind == "tool" and text == "read_memory pid=1 linha 149 çãé" and ts.isdigit()
    _, body = _get(b, "/v1/feed?since=148")
    assert [ln.split("\t")[0] for ln in body.splitlines()] == ["149", "150"]
    _, body = _get(b, "/v1/state")
    state = dict(ln.split("=", 1) for ln in body.splitlines())
    assert state["attached_pid"] == "77" and state["process"] == "" and state["messages_pending"] == "0"

    req = urllib.request.Request(f"http://127.0.0.1:{b.port}/v1/message", data="continue, por favor ç".encode(), method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        assert r.read() == b"ok 1\n"
    assert b.has_messages()
    assert b.pop_messages()[0]["text"] == "continue, por favor ç"
    assert not b.has_messages()
    _, body = _get(b, "/v1/feed?since=150")
    assert "\tuser\tvocê: continue" in body


def test_bridge_rejects_bad_requests(bridge):
    b, _ = bridge
    req = urllib.request.Request(f"http://127.0.0.1:{b.port}/v1/message", data=b"x" * 600, method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=3)
    assert err.value.code == 400
    assert b.allowed("127.0.0.1") and not b.allowed("192.168.0.99")
    open_bridge = Bridge.__new__(Bridge)
    open_bridge.allowed_ip = ""
    assert open_bridge.allowed("192.168.0.99") and not open_bridge.allowed("8.8.8.8")


class FakeRPI:
    """Remote Package Installer falso: baixa o pacote do PC em pedaços com Range, como o console."""

    def __init__(self):
        self.downloaded = bytearray()
        self.total = 0
        rpi = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, raw=None):
                body = raw or json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/install":
                    url = payload["packages"][0]
                    if url.endswith("broken.pkg"):
                        return self._json(None, b'{ "status": "fail", "error_code": 0x80990088 }')
                    head = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=3)
                    rpi.total = int(head.headers["Content-Length"])
                    threading.Thread(target=rpi._download, args=(url,), daemon=True).start()
                    return self._json({"status": "success", "task_id": 7, "title": "CCPS4 Crash Test"})
                if self.path == "/api/get_task_progress":
                    return self._json({"status": "success", "error": 0, "length_total": rpi.total,
                                       "transferred_total": len(rpi.downloaded)})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def _download(self, url):
        step = max(1, self.total // 3)
        for start in range(0, self.total, step):
            end = min(start + step, self.total) - 1
            req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(req, timeout=3) as r:
                assert r.status == 206
                self.downloaded += r.read()


def test_install_pkg_via_fake_rpi(tmp_path):
    pkg = tmp_path / "CCPS4 Crash Test.pkg"
    content = bytes(range(256)) * 4000
    pkg.write_bytes(content)
    rpi = FakeRPI()
    try:
        res = install_pkg("127.0.0.1", pkg, rpi_port=rpi.httpd.server_address[1], pc_ip="127.0.0.1", poll_s=0.1, timeout=10)
        assert res["task_id"] == 7 and res["bytes"] == len(content)
        assert hashlib.sha256(rpi.downloaded).digest() == hashlib.sha256(content).digest()
        broken = tmp_path / "broken.pkg"
        broken.write_bytes(b"x")
        with pytest.raises(DeployError, match="recusou"):
            install_pkg("127.0.0.1", broken, rpi_port=rpi.httpd.server_address[1], pc_ip="127.0.0.1")
    finally:
        rpi.httpd.shutdown()


def test_lenient_json_hex_literals():
    assert _lenient_json(b'{"status":"fail","error_code": 0x80990088}')["error_code"] == "0x80990088"
