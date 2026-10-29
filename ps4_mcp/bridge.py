"""Bridge PC ↔ app "CCPS4 Agent Viewer" no PS4 (fase 6, opcional, liga com PS4_BRIDGE=1).

Protocolo v1 (o app em ps4-app/ implementa o outro lado):
- descoberta: datagrama UDP para 255.255.255.255:8766 a cada 2 s, "CCPS4-BRIDGE v1 http=<porta>\\n";
- HTTP/1.0, uma requisição por conexão, sempre com Content-Length;
- GET /v1/feed?since=N → linhas "<seq>\\t<unix_ts>\\t<tipo>\\t<texto>\\n" (até 100, seq > N; N=0 → últimas 100);
- GET /v1/state → linhas "chave=valor";
- POST /v1/message (texto UTF-8, até 512 bytes) → "ok <id>".
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

KINDS = {"tool", "result", "event", "status", "user", "info", "error"}
MAX_TEXT_BYTES = 300
MAX_MESSAGE_BYTES = 512
FEED_PAGE = 100


def _clean(text: str) -> str:
    text = " ".join(str(text).replace("\t", " ").split())
    raw = text.encode("utf-8")[:MAX_TEXT_BYTES]
    return raw.decode("utf-8", "ignore")


class Bridge:
    def __init__(self, port: int = 8765, discovery_port: int = 8766, allowed_ip: str = "",
                 host: str = "0.0.0.0", state_fn: Callable[[], dict] | None = None,
                 broadcast_interval: float = 2.0):
        self.allowed_ip = allowed_ip
        self.discovery_port = discovery_port
        self.broadcast_interval = broadcast_interval
        self.state_fn = state_fn or (lambda: {})
        self._feed: deque[tuple[int, int, str, str]] = deque(maxlen=1000)
        self._messages: deque[dict] = deque(maxlen=100)
        self._seq = self._msg_id = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._httpd = ThreadingHTTPServer((host, port), self._handler())
        self._httpd.daemon_threads = True

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self) -> "Bridge":
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="bridge-http").start()
        threading.Thread(target=self._announce, daemon=True, name="bridge-udp").start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._httpd.shutdown()
        self._httpd.server_close()

    def add(self, kind: str, text: str) -> int:
        kind = kind if kind in KINDS else "info"
        with self._lock:
            self._seq += 1
            self._feed.append((self._seq, int(time.time()), kind, _clean(text)))
            return self._seq

    def has_messages(self) -> bool:
        return bool(self._messages)

    def pop_messages(self) -> list[dict]:
        with self._lock:
            out = list(self._messages)
            self._messages.clear()
            return out

    def feed_since(self, since: int) -> list[tuple[int, int, str, str]]:
        with self._lock:
            entries = [e for e in self._feed if e[0] > since]
        return entries[-FEED_PAGE:] if since == 0 else entries[:FEED_PAGE]

    def allowed(self, ip: str) -> bool:
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback:
            return True
        if self.allowed_ip:
            return ip == self.allowed_ip
        return addr.is_private

    def _post_message(self, text: str) -> int:
        with self._lock:
            self._msg_id += 1
            self._messages.append({"id": self._msg_id, "text": text, "at": time.time()})
            msg_id = self._msg_id
        self.add("user", f"você: {text}")
        return msg_id

    def _announce(self) -> None:
        payload = f"CCPS4-BRIDGE v1 http={self.port}\n".encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        targets = [("255.255.255.255", self.discovery_port)]
        if self.allowed_ip:
            targets.append((self.allowed_ip, self.discovery_port))
        while True:
            for target in targets:
                try:
                    sock.sendto(payload, target)
                except OSError:
                    pass
            if self._stop.wait(self.broadcast_interval):
                break
        sock.close()

    def _handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def _send(self, code: int, body: str) -> None:
                raw = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)

            def _guard(self) -> bool:
                if bridge.allowed(self.client_address[0]):
                    return True
                self._send(403, "forbidden\n")
                return False

            def do_GET(self):
                if not self._guard():
                    return
                url = urlparse(self.path)
                if url.path == "/v1/feed":
                    try:
                        since = int(parse_qs(url.query).get("since", ["0"])[0])
                    except ValueError:
                        return self._send(400, "bad since\n")
                    body = "".join(f"{s}\t{ts}\t{k}\t{t}\n" for s, ts, k, t in bridge.feed_since(since))
                    return self._send(200, body)
                if url.path == "/v1/state":
                    state = bridge.state_fn() | {"messages_pending": len(bridge._messages)}
                    body = "".join(f"{k}={'' if v is None else v}\n" for k, v in state.items())
                    return self._send(200, body)
                self._send(404, "not found\n")

            def do_POST(self):
                if not self._guard():
                    return
                if urlparse(self.path).path != "/v1/message":
                    return self._send(404, "not found\n")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if not 0 < length <= MAX_MESSAGE_BYTES:
                    return self._send(400, "body vazio ou maior que 512 bytes\n")
                text = self.rfile.read(length).decode("utf-8", "replace").strip()
                if not text:
                    return self._send(400, "mensagem vazia\n")
                self._send(200, f"ok {bridge._post_message(text)}\n")

        return Handler
