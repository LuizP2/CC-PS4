#!/usr/bin/env python3
"""Stub do bridge v1 (lado PC) para testar o CCPS4 Agent Viewer no host. Só stdlib.

Implementa o protocolo v1 como o ps4_mcp/bridge.py:
  - anúncio UDP "CCPS4-BRIDGE v1 http=<porta>\\n" para 255.255.255.255:<udp> a cada
    --interval s (e cópias unicast opcionais com --unicast, para hosts sem broadcast);
  - HTTP/1.0, Connection: close, sempre com Content-Length;
  - GET /v1/feed?since=N   (até 100 entradas com seq > N; N=0 -> as 100 últimas)
  - GET /v1/state
  - POST /v1/message       (UTF-8, 1..512 bytes; 200 "ok <id>", 400, 403)
Extras só de teste:
  - POST /test/stop        desliga o stub (para testar "desconectado")
  - --inject-malformed     acrescenta linhas inválidas em toda resposta do feed
  - --deny-messages        responde 403 ao POST /v1/message
  - --messages-out ARQ     grava cada mensagem recebida (JSON por linha)
  - --port-file ARQ        grava a porta HTTP escolhida (útil com --http-port 0)
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

KINDS = {"tool", "result", "event", "status", "user", "info", "error"}
MAX_TEXT = 300
MAX_MSG = 512


def clean(text: str) -> str:
    text = " ".join(text.replace("\t", " ").split())
    return text.encode("utf-8")[:MAX_TEXT].decode("utf-8", "ignore")


class Stub:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.feed: list[tuple[int, int, str, str]] = []
        self.seq = 0
        self.msg_id = 0
        self.messages: list[dict] = []
        self.stop = threading.Event()
        self.state = {
            "agent": "waiting_event", "ps4debug": "connected", "attached_pid": "87",
            "process": "eboot.bin", "paused": "1", "pending_events": "1",
            "last_tool": "wait_for_event", "messages_pending": "0",
            "campo_futuro": "ignorado-pelo-app",
        }
        self.seed()

    def add(self, kind: str, text: str, ts: int | None = None) -> None:
        with self.lock:
            self.seq += 1
            self.feed.append((self.seq, int(ts if ts is not None else time.time()),
                              kind if kind in KINDS else "info", clean(text)))

    def seed(self) -> None:
        t = int(time.time()) - 120
        rows = [
            ("status", "Sessão iniciada: investigando o crash do CCPS4 Crash Test (CCPS00002)."),
            ("tool", "ps4_status()"),
            ("result", "conectado; app em primeiro plano: CCPS00002 \"CCPS4 Crash Test\" v1.00"),
            ("tool", "list_processes()"),
            ("result", "pid 87 eboot.bin · pid 52 SceShellUI · pid 43 SceSysCore (+31 processos)"),
            ("tool", "attach(87)"),
            ("result", "anexado ao pid 87; base do eboot.bin = 0x0000000000400000"),
            ("tool", "set_breakpoint(\"eboot.bin+0x20\")"),
            ("result", "breakpoint #1 em 0x400020 (crash_here)"),
            ("tool", "set_watchpoint(0x4141e0, 4, \"write\")"),
            ("result", "watchpoint #0 (escrita, 4 bytes) em g_watch_counter"),
            ("tool", "search_memory(87, \"Olá, ação\", \"utf-8\")"),
            ("result", "2 ocorrências: 0x404eb8 (eboot.bin, .rodata, r--) e 0x2000a4c10 (heap, rw-). "
                       "A cópia no heap é refeita a cada tick, então a legenda “viva” é a do heap — "
                       "é nela que um watchpoint pegaria a rotina que escreve o texto."),
            ("tool", "wait_for_event(timeout_s=120)"),
            ("event", "watchpoint #0 disparou: tid 101, rip eboot.bin+0x9c (main), g_watch_counter 41 → 42"),
            ("info", "Processo pausado no debugger; o tempo do jogo para enquanto isso."),
            ("tool", "resume()"),
            ("event", "breakpoint #1: rip eboot.bin+0x20 (crash_here), rdi = 0x0 — o argumento p é NULL"),
            ("error", "SIGSEGV em eboot.bin+0x38 (crash_here+0x18): mov eax, dword ptr [rax] com rax = 0x8"),
            ("status", "Causa: crash_here(NULL) lê p->value (offset 8) — ponteiro nulo vindo de main."),
        ]
        for i, (k, txt) in enumerate(rows):
            self.add(k, txt, t + i * 5)

    def feed_since(self, since: int):
        with self.lock:
            entries = [e for e in self.feed if e[0] > since]
        return entries[-100:] if since == 0 else entries[:100]


def make_handler(stub: Stub, httpd_ref: dict):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *a):
            if stub.args.verbose:
                super().log_message(fmt, *a)

        def send_text(self, code: int, body: str) -> None:
            raw = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/v1/feed":
                try:
                    since = int(parse_qs(url.query).get("since", ["0"])[0])
                except ValueError:
                    return self.send_text(400, "bad since\n")
                body = "".join(f"{s}\t{ts}\t{k}\t{t}\n" for s, ts, k, t in stub.feed_since(since))
                if stub.args.inject_malformed:
                    body = "isto não é uma linha válida\n" + body + "x\ty\tz\n0\t1\tinfo\tseq zero\n"
                return self.send_text(200, body)
            if url.path == "/v1/state":
                with stub.lock:
                    st = dict(stub.state, messages_pending=str(len(stub.messages)))
                return self.send_text(200, "".join(f"{k}={v}\n" for k, v in st.items()))
            self.send_text(404, "not found\n")

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/test/stop":
                self.send_text(200, "bye\n")
                threading.Thread(target=httpd_ref["httpd"].shutdown, daemon=True).start()
                stub.stop.set()
                return
            if path != "/v1/message":
                return self.send_text(404, "not found\n")
            if stub.args.deny_messages:
                return self.send_text(403, "forbidden\n")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_MSG:
                return self.send_text(400, "body vazio ou maior que 512 bytes\n")
            raw = self.rfile.read(length)
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                return self.send_text(400, "mensagem vazia\n")
            with stub.lock:
                stub.msg_id += 1
                mid = stub.msg_id
                stub.messages.append({"id": mid, "text": text, "raw_hex": raw.hex(),
                                      "from": self.client_address[0]})
            if stub.args.messages_out:
                with open(stub.args.messages_out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(stub.messages[-1], ensure_ascii=False) + "\n")
            stub.add("user", f"você: {text}")
            self.send_text(200, f"ok {mid}\n")

    return Handler


def announce(stub: Stub, port: int) -> None:
    payload = f"CCPS4-BRIDGE v1 http={port}\n".encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    targets = [("255.255.255.255", stub.args.udp_port)] + [(ip, stub.args.udp_port) for ip in stub.args.unicast]
    while not stub.stop.is_set():
        for t in targets:
            try:
                sock.sendto(payload, t)
            except OSError:
                pass
        stub.stop.wait(stub.args.interval)
    sock.close()


def live(stub: Stub) -> None:
    n = 0
    while not stub.stop.wait(stub.args.live):
        n += 1
        stub.add("info", f"atividade simulada #{n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http-port", type=int, default=8765)
    ap.add_argument("--udp-port", type=int, default=8766)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--unicast", action="append", default=[])
    ap.add_argument("--messages-out")
    ap.add_argument("--port-file")
    ap.add_argument("--inject-malformed", action="store_true")
    ap.add_argument("--deny-messages", action="store_true")
    ap.add_argument("--live", type=float, default=0.0, help="nova entrada a cada N s (0 = não)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    stub = Stub(args)
    ref: dict = {}
    httpd = ThreadingHTTPServer((args.bind, args.http_port), make_handler(stub, ref))
    httpd.daemon_threads = True
    ref["httpd"] = httpd
    port = httpd.server_address[1]
    if args.port_file:
        with open(args.port_file, "w") as fh:
            fh.write(f"{port}\n")
    print(f"[stub] HTTP em {args.bind}:{port}, anúncio UDP para :{args.udp_port}", flush=True)
    threading.Thread(target=announce, args=(stub, port), daemon=True).start()
    if args.live > 0:
        threading.Thread(target=live, args=(stub,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    stub.stop.set()
    httpd.server_close()
    print(f"[stub] encerrado; {len(stub.messages)} mensagem(ns) recebida(s)", flush=True)


if __name__ == "__main__":
    main()
