"""Servidor MCP de ponta a ponta: processo real via stdio, como o Claude Code o executa, contra
ps4debug falso + FTP real (pyftpdlib) + klog falso + bridge do app."""

import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.request

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitResult
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from ps4_mcp.sfo import build_sfo
from tests.native_blob import Blob, needs_x86_64
from tests.test_e2e_debug import CODE, CODE_BASE, PID, free_port, make_console

SUBTITLE = "CCPS4 legenda de teste: Olá, ação, coração!"


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def lab(tmp_path):
    cb = free_port()
    fake, proc = make_console(cb)
    proc.write(0x2000100, SUBTITLE.encode("utf-8") + b"\0")
    proc.write(0x2000401, SUBTITLE.encode("utf-16le") + b"\0\0")  # desalinhado de propósito
    proc.write(CODE_BASE + 0x4000, (0x2000800).to_bytes(8, "little"))
    proc.write(0x2000810, (0x2000100).to_bytes(8, "little"))

    root = tmp_path / "ftp"
    meta = root / "system_data/priv/appmeta/CUSA12345"
    meta.mkdir(parents=True)
    (meta / "param.sfo").write_bytes(build_sfo({"TITLE": "Jogo de Teste", "APP_VER": "01.02", "VERSION": "01.00", "CATEGORY": "gd"}))
    (root / "data").mkdir()
    (root / "data/config.ini").write_text("[video]\nlang=pt-BR\n")
    auth = DummyAuthorizer()
    auth.add_anonymous(str(root))
    handler = type("H", (FTPHandler,), {"authorizer": auth})
    ftpd = FTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=ftpd.serve_forever, kwargs={"handle_exit": False}, daemon=True).start()

    klog = socket.socket()
    klog.bind(("127.0.0.1", 0))
    klog.listen(1)

    def serve_klog():
        conn, _ = klog.accept()
        for i in range(1000):
            try:
                conn.sendall(f"[CCPS4-CRASHTEST] tick {i}/90 counter={i}\n".encode())
            except OSError:
                return
            time.sleep(0.05)

    threading.Thread(target=serve_klog, daemon=True).start()
    bridge_port = free_port()
    env = os.environ | {
        "PS4_IP": "127.0.0.1", "PS4DEBUG_PORT": str(fake.port), "PS4DEBUG_CALLBACK_PORT": str(cb),
        "PS4DEBUG_CALLBACK_BIND": "127.0.0.1", "PS4_FTP_PORT": str(ftpd.address[1]),
        "PS4_LOG_SOURCE": f"tcp:127.0.0.1:{klog.getsockname()[1]}", "PS4_WORKDIR": str(tmp_path / "work"),
        "PS4_BRIDGE": "1", "PS4_BRIDGE_PORT": str(bridge_port), "PS4_BRIDGE_DISCOVERY_PORT": str(free_udp_port()),
        "PS4_ENABLE_WRITE": "1",
    }
    yield {"fake": fake, "proc": proc, "env": env, "work": tmp_path / "work", "bridge_port": bridge_port}
    ftpd.close_all()
    klog.close()
    fake.stop()


async def _session(env: dict, scenario, answer: bool = True):
    async def on_elicit(context, params):
        return ElicitResult(action="accept", content={"confirmar": True}) if answer else ElicitResult(action="decline")

    params = StdioServerParameters(command=sys.executable, args=["-m", "ps4_mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, elicitation_callback=on_elicit) as session:
            await session.initialize()
            return await scenario(session)


async def call(session: ClientSession, name: str, **args):
    res = await session.call_tool(name, args)
    text = res.content[0].text if res.content else ""
    if res.isError:
        raise AssertionError(f"{name} falhou: {text}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_full_investigation_over_mcp(lab):
    fake, proc, work = lab["fake"], lab["proc"], lab["work"]

    async def scenario(s: ClientSession):
        names = {t.name for t in (await s.list_tools()).tools}
        expected = {"ps4_status", "ps4_diagnose", "list_processes", "process_maps", "read_memory", "read_pointer_chain",
                    "read_string", "resolve_address", "disassemble", "list_dir", "read_file", "download_file", "tail_logs",
                    "attach", "detach", "set_breakpoint", "clear_breakpoint", "set_watchpoint", "clear_watchpoint",
                    "wait_for_event", "get_registers", "list_threads", "step", "resume", "pause", "dump_module",
                    "search_memory", "scan_value", "rescan", "read_text_region", "search_files", "compare_bytes", "mark_milestone",
                    "notify_console", "post_status", "get_messages", "write_memory", "kill_process", "kernel_read"}
        assert expected <= names, expected - names
        assert "install_pkg" not in names  # deploy desligado

        await call(s, "mark_milestone", label="inicio", note="teste e2e")
        status = await call(s, "ps4_status")
        assert status["ps4debug"]["version"] == "1.2"
        game = status["games"][0]
        assert game["titleid"] == "CUSA12345" and game["title"] == "Jogo de Teste" and game["app_ver"] == "01.02"
        assert "eboot.bin" in await call(s, "list_processes")
        assert "[anon:heap]" in await call(s, "process_maps", pid=PID)
        dump = await call(s, "read_memory", pid=PID, address="eboot.bin+0x0", size=16)
        assert "0000000000400000  55 48 89 e5" in dump
        big = await call(s, "read_memory", pid=PID, address="0x2000000", size=8192)
        assert os.path.exists(big["saved_to"]) and big["summary"]["size"] == 8192
        chain = await call(s, "read_pointer_chain", pid=PID, base="eboot.bin+0x4000", offsets=["0x10", "0x0"])
        assert chain["final_address"] == "0x2000100" and chain["as_text"].startswith("CCPS4 legenda")
        found = await call(s, "search_memory", pid=PID, text="Olá, ação")
        assert {m["encoding"] for m in found["matches"]} == {"utf-8", "utf-16le"}
        assert "Olá" in (await call(s, "read_text_region", pid=PID, address="0x2000401", size=64))["text"]
        assert (await call(s, "resolve_address", pid=PID, address="0x400004"))["module_offset"] == "0x4"
        assert "push rbp" in await call(s, "disassemble", pid=PID, address="eboot.bin", count=4)

        assert "data" in await call(s, "list_dir", path="/")
        assert "lang=pt-BR" in await call(s, "read_file", path="/data/config.ini")
        got = await call(s, "download_file", path="/data/config.ini")
        assert open(got["local_path"]).read().startswith("[video]")
        await asyncio.sleep(0.5)
        assert "[CCPS4-CRASHTEST] tick" in await call(s, "tail_logs", n=5)

        attached = await call(s, "attach")
        assert attached["attached_pid"] == PID and "eboot.bin" in attached["executable_modules"]
        bp = await call(s, "set_breakpoint", address="eboot.bin+0x4")
        assert bp["instruction"].endswith("mov eax, dword ptr [rax + 8]")
        threading.Timer(1.0, fake.hit_breakpoint, args=(bp["id"],), kwargs={"lwpid": 101}).start()
        event = await call(s, "wait_for_event", timeout_s=10)
        assert event["event"] == "breakpoint" and event["module_offset"] == "0x4" and event["thread_name"] == "MainThread"
        assert any("tick" in line for line in event["recent_logs"])
        regs = await call(s, "get_registers")
        assert regs["thread_id"] == 101 and regs["registers"]["rsp"] == hex(0x7F0000)
        assert "RenderThread" in await call(s, "list_threads")
        assert (await call(s, "resume"))["state"] == "running"

        timeout = await call(s, "wait_for_event", timeout_s=1)
        assert timeout["event"] == "timeout"
        req = urllib.request.Request(f"http://127.0.0.1:{lab['bridge_port']}/v1/message", data="pare e explique".encode(), method="POST")
        threading.Timer(0.5, urllib.request.urlopen, args=(req,)).start()
        msg = await call(s, "wait_for_event", timeout_s=5)
        assert msg["event"] == "user_message" and msg["messages"][0]["text"] == "pare e explique"
        await call(s, "post_status", text="investigando o acesso nulo")
        with urllib.request.urlopen(f"http://127.0.0.1:{lab['bridge_port']}/v1/feed?since=0") as r:
            feed = r.read().decode()
        assert "\tstatus\tinvestigando o acesso nulo" in feed and "\ttool\tset_breakpoint" in feed

        wrote = await call(s, "write_memory", pid=PID, address="0x2000000", hex_bytes="deadbeef")
        assert wrote["written"] == 4 and proc.read(0x2000000, 4) == bytes.fromhex("deadbeef")
        elf = await call(s, "dump_module", pid=PID, module="eboot.bin")
        with open(elf["saved_to"], "rb") as fh:
            content = fh.read()
        assert content[:4] == b"\x7fELF"
        assert CODE in content  # breakpoint armado em +0x4 não vaza 0xCC para o dump
        await call(s, "detach")
        assert fake.attached_pid is None and proc.read(CODE_BASE + 4, 1) == b"\x8b"
        diag = await call(s, "ps4_diagnose")
        assert {c["check"]: c["ok"] for c in diag["checks"]}["ps4debug"]
        await call(s, "mark_milestone", label="causa_encontrada")

    asyncio.run(_session(lab["env"], scenario))
    assert not fake.payload_crashed
    audit = (work / "logs" / "audit.jsonl").read_text().splitlines()
    assert any('"kind": "event"' in line or '"kind":"event"' in line for line in audit)


def test_value_scan_and_rescan(lab):
    proc = lab["proc"]
    proc.write(0x2000200, (37).to_bytes(4, "little"))  # as "moedas"
    proc.write(0x2000300, (37).to_bytes(4, "little"))  # outro 37 qualquer, que não muda
    proc.write(0x2000501, (37).to_bytes(4, "little"))  # desalinhado: fica de fora
    proc.write(0x2000600, (-5).to_bytes(4, "little", signed=True))

    async def scenario(s: ClientSession):
        first = await call(s, "scan_value", pid=PID, value="37")
        assert first["candidates"] == 2 and {m["address"] for m in first["matches"]} == {"0x2000200", "0x2000300"}
        proc.write(0x2000200, (38).to_bytes(4, "little"))
        up = await call(s, "rescan", scan_id=first["scan_id"], condition="increased")
        assert up["candidates"] == 1 and up["matches"][0] == {"address": "0x2000200", "where": up["matches"][0]["where"], "value": 38}
        assert (await call(s, "rescan", scan_id=first["scan_id"], condition="38"))["candidates"] == 1
        assert (await call(s, "rescan", scan_id=first["scan_id"], condition="unchanged"))["candidates"] == 1
        same = await call(s, "rescan", scan_id=first["scan_id"], condition="0x26")
        assert same["candidates"] == 1
        gone = await call(s, "rescan", scan_id=first["scan_id"], condition="decreased")
        assert gone["candidates"] == 0 and gone["matches"] == []

        neg = await call(s, "scan_value", pid=PID, value="-5", type="i32", regions="heap")
        assert neg["scan_id"] == first["scan_id"] + 1 and [m["address"] for m in neg["matches"]] == ["0x2000600"]
        bad = await s.call_tool("rescan", {"scan_id": 999, "condition": "1"})
        assert bad.isError and "scan_value" in bad.content[0].text
        bad = await s.call_tool("scan_value", {"pid": PID, "value": "300", "type": "u8"})
        assert bad.isError and "não cabe" in bad.content[0].text

    asyncio.run(_session(lab["env"], scenario))
    assert not lab["fake"].payload_crashed


@needs_x86_64
def test_value_scan_runs_inside_the_game(lab):
    fake, proc = lab["fake"], lab["proc"]
    proc.map("libkernel.sprx", 0x800000000, 0x4000, prot=5)  # sem ela o payload não cria a thread do stub
    proc.on_call_code(Blob("scan_value").code, Blob("scan_value").in_fake(proc))
    proc.write(0x2000200, (37).to_bytes(4, "little"))
    proc.write(0x2000300, (37).to_bytes(4, "little"))

    async def scenario(s: ClientSession):
        first = await call(s, "scan_value", pid=PID, value="37")
        assert first["engine"] == "in-game" and first["candidates"] == 2
        assert {m["address"] for m in first["matches"]} == {"0x2000200", "0x2000300"}
        again = await call(s, "scan_value", pid=PID, value="37")  # o stub é reaproveitado e os buffers do MCP ficam fora
        assert again["engine"] == "in-game" and again["candidates"] == 2
        proc.write(0x2000200, (38).to_bytes(4, "little"))
        assert (await call(s, "rescan", scan_id=first["scan_id"], condition="38"))["matches"][0]["address"] == "0x2000200"
        await call(s, "attach")
        attached = await call(s, "scan_value", pid=PID, value="37")  # anexado: um breakpoint travaria o PROC_CALL
        assert attached["engine"] == "network" and "detach" in attached["engine_note"]
        await call(s, "detach")

    asyncio.run(_session(lab["env"] | {"PS4_INGAME_SCAN": "1"}, scenario))
    assert [c for c, _ in fake.commands].count("CMD_PROC_INTALL") == 1
    assert not fake.payload_crashed


def test_value_scan_never_calls_a_dead_rpc_stub(lab):
    fake, proc = lab["fake"], lab["proc"]  # sem libkernel.sprx: o PROC_INSTALL não cria a thread do stub
    proc.write(0x2000200, (37).to_bytes(4, "little"))

    async def scenario(s: ClientSession):
        res = await call(s, "scan_value", pid=PID, value="37")
        assert res["engine"] == "network" and res["candidates"] == 1 and "thread" in res["engine_note"]
        assert (await call(s, "ps4_status"))["ps4debug"]["version"]  # payload continua atendendo

    asyncio.run(_session(lab["env"] | {"PS4_INGAME_SCAN": "1"}, scenario))
    assert "CMD_PROC_CALL" not in [c for c, _ in fake.commands]


def test_write_needs_manual_confirmation_and_flags(lab):
    async def declined(s: ClientSession):
        res = await s.call_tool("write_memory", {"pid": PID, "address": "0x2000000", "hex_bytes": "00"})
        assert res.isError and "cancelada" in res.content[0].text

    lab["proc"].write(0x2000000, b"\x11")
    asyncio.run(_session(lab["env"], declined, answer=False))
    assert lab["proc"].read(0x2000000, 1) == b"\x11"  # recusado: nada foi escrito

    env = lab["env"] | {"PS4_ENABLE_WRITE": "0", "PS4_BRIDGE": "0"}

    async def readonly(s: ClientSession):
        names = {t.name for t in (await s.list_tools()).tools}
        assert not names & {"write_memory", "kill_process", "kernel_read", "install_pkg", "build_homebrew"}
        res = await s.call_tool("get_messages", {})
        assert res.isError and "bridge desligado" in res.content[0].text

    asyncio.run(_session(env, readonly))


def test_refuses_public_ip(lab):
    env = lab["env"] | {"PS4_IP": "8.8.8.8"}

    async def scenario(s: ClientSession):
        res = await s.call_tool("list_processes", {})
        assert res.isError and "rede local" in res.content[0].text

    asyncio.run(_session(env, scenario))
