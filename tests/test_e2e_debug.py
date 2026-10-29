"""Cliente + sessão de debug contra o ps4debug falso (escrito de forma independente a partir do C)."""

import socket

import pytest

from ps4_mcp.client import PS4Debug, PS4DebugError, StatusError, unmapped_warning
from ps4_mcp.config import Config
from ps4_mcp.debug import DebugSession
from ps4_mcp.protocol import BREAKPT, Cmd
from ps4_mcp.snapshot import build_snapshot
from tests.fakes.fake_ps4debug import FakePS4Debug

PID = 0x55
CODE_BASE = 0x400000
CODE = bytes.fromhex("554889e58b40085dc3")  # push rbp; mov rbp,rsp; mov eax,[rax+8]; pop rbp; ret
STACK = 0x7F0000


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_console(callback_port: int):
    fake = FakePS4Debug(port=0, debug_port=callback_port).start()
    proc = fake.add_process(PID, "eboot.bin", titleid="CUSA12345", contentid="EP0000-CUSA12345_00-TESTGAME00000000", path="/app0/eboot.bin")
    proc.map("eboot.bin", CODE_BASE, CODE + bytes(0x4000 - len(CODE)), prot=5)
    proc.map("eboot.bin", CODE_BASE + 0x4000, 0x4000, prot=3)
    proc.map("[anon:heap]", 0x2000000, 0x10000, prot=3)
    stack = bytearray(0x4000)
    stack[0x10:0x18] = (CODE_BASE + 7).to_bytes(8, "little")
    proc.map("stack", STACK, bytes(stack), prot=3)
    # O payload real omite a última região do mapa (varredura começa no sentinela do vm_map);
    # num processo real ela é a de endereço mais alto, então pomos uma aqui.
    proc.map("libSceTop.sprx", 0x900000000, 0x4000, prot=5)
    proc.add_thread(101, "MainThread", regs={"rip": CODE_BASE + 4, "rsp": STACK, "rax": 0})
    proc.add_thread(102, "RenderThread", regs={"rip": CODE_BASE, "rsp": STACK + 0x100})
    return fake, proc


@pytest.fixture
def console(tmp_path):
    cb = free_port()
    fake, proc = make_console(cb)
    cfg = Config(ps4_ip="127.0.0.1", debug_port=fake.port, callback_port=cb, callback_bind="127.0.0.1", workdir=tmp_path)
    client = PS4Debug("127.0.0.1", fake.port)
    session = DebugSession(cfg, client)
    yield fake, proc, cfg, client, session
    try:
        session.detach()
    except Exception:
        pass
    client.close()
    fake.stop()


def test_process_primitives(console):
    fake, proc, _, client, _ = console
    assert client.version() == "1.2"
    assert (PID, "eboot.bin") in client.processes()
    info = client.process_info(PID)
    assert info["titleid"] == "CUSA12345" and info["path"] == "/app0/eboot.bin"
    maps = client.maps(PID)
    assert [(m.name, m.start, m.prot) for m in maps][:2] == [("eboot.bin", CODE_BASE, 5), ("eboot.bin", CODE_BASE + 0x4000, 3)]
    assert client.read(PID, CODE_BASE, len(CODE)) == CODE
    assert client.read(PID, 0x10, 16) == bytes(16)  # não mapeado → zeros, como o payload real
    assert unmapped_warning(maps, 0x10, 16)
    big = client.read(PID, 0x2000000, 0x10000)  # várias fatias de 8 KiB do lado do payload
    assert len(big) == 0x10000
    assert not fake.payload_crashed


def test_breakpoint_hit_snapshot_and_detach_restores(console):
    fake, proc, _, client, session = console
    session.attach(PID)
    assert fake.attached_pid == PID and session.state == "running"
    bp = session.set_breakpoint(CODE_BASE + 4, "acesso suspeito")
    assert fake.breakpoints[bp["id"]] == (CODE_BASE + 4, 0x8B)
    assert proc.read(CODE_BASE + 4, 1) == b"\xcc"
    with pytest.raises(PS4DebugError, match="já existe"):
        session.set_breakpoint(CODE_BASE + 4)
    with pytest.raises(PS4DebugError, match="execução"):
        session.set_breakpoint(CODE_BASE + 0x4000)

    fake.hit_breakpoint(bp["id"], lwpid=101)
    event = session.wait_event(3)
    assert event["event"] == "breakpoint" and event["breakpoint_id"] == bp["id"]
    assert event["thread_id"] == 101 and event["thread_name"] == "MainThread"
    assert session.state == "stopped" and fake.process_state == "stopped"

    snap = build_snapshot(event, client, session.breakpoints, ["log 1"])
    assert snap["module"] == "eboot.bin" and snap["module_offset"] == "0x4"
    assert snap["disasm"][0].endswith("mov eax, dword ptr [rax + 8]")  # byte original, não 0xCC
    assert snap["stack_code_pointers"][0]["where"] == "eboot.bin+0x7"
    assert snap["recent_logs"] == ["log 1"]

    regs = session.get_registers(101)
    assert regs["rsp"] == STACK
    assert set(session.threads()) == {101, 102}
    assert session.thread_info(102)["name"] == "RenderThread"

    session.resume()
    assert fake.process_state == "running"
    session.detach()
    assert fake.attached_pid is None and not fake.g_debugging
    assert proc.read(CODE_BASE + 4, 1) == b"\x8b"
    assert not fake.payload_crashed


def test_crash_snapshot_guesses_null_pointer(console):
    fake, _, _, client, session = console
    session.attach(PID)
    fake.raise_signal(11, lwpid=101, rip=CODE_BASE + 4, regs={"rax": 0, "rsp": STACK})
    event = session.wait_event(3)
    assert event["event"] == "crash" and event["signal"] == "SIGSEGV"
    snap = build_snapshot(event, client, session.breakpoints, [])
    assert snap["fault_guess"]["address"] == "0x8"
    assert "ponteiro nulo" in snap["fault_guess"]["note"]


def test_watchpoint_pause_and_step(console):
    fake, _, _, _, session = console
    session.attach(PID)
    with pytest.raises(PS4DebugError, match="alinhado"):
        session.set_watchpoint(CODE_BASE + 0x4002, 4, "write")
    wp = session.set_watchpoint(CODE_BASE + 0x4000, 4, "write")
    assert fake.watchpoints[wp["id"]] == (CODE_BASE + 0x4000, 0x03, 0x01)
    fake.hit_watchpoint(wp["id"], lwpid=101, rip=CODE_BASE + 7)
    event = session.wait_event(3)
    assert event["event"] == "watchpoint" and event["watchpoint_id"] == wp["id"]
    session.clear_watchpoint(wp["id"])
    assert wp["id"] not in fake.watchpoints

    session.resume()
    with pytest.raises(PS4DebugError, match="rodando"):
        session.get_registers(101)
    session.pause()
    assert fake.process_state == "stopped"
    assert session.wait_event(0.5) is None  # SIGSTOP não gera pacote de interrupção
    session.step()
    event = session.wait_event(3)
    assert event["event"] == "step"


def test_debug_command_error_keeps_session(console):
    fake, _, _, _, session = console
    session.attach(PID)
    with pytest.raises(StatusError):
        session._cmd(Cmd.DEBUG_BREAKPT, BREAKPT.pack(40, 1, CODE_BASE))  # índice inválido
    assert fake.attached_pid == PID and session.attached  # só a conexão de comandos caiu
    assert session.set_breakpoint(CODE_BASE)["id"] == 0
    assert not fake.payload_crashed


def test_limits_and_existing_int3(console):
    fake, proc, cfg, _, session = console
    cfg.max_breakpoints = 2
    session.attach(PID)
    session.set_breakpoint(CODE_BASE)
    session.set_breakpoint(CODE_BASE + 1)
    with pytest.raises(PS4DebugError, match="limite"):
        session.set_breakpoint(CODE_BASE + 4)
    proc.write(CODE_BASE + 0x100, b"\xcc")
    session.clear_breakpoint(1)
    with pytest.raises(PS4DebugError, match="0xCC"):
        session.set_breakpoint(CODE_BASE + 0x100)


def test_process_exit_ends_session_without_crashing_payload(console):
    fake, _, _, _, session = console
    session.attach(PID)
    fake.exit_process()
    event = session.wait_event(3)
    assert event["event"] == "detached"
    assert not session.attached
    with pytest.raises(PS4DebugError, match="não há sessão"):
        session.set_breakpoint(CODE_BASE)
    assert not fake.payload_crashed


def test_attach_fails_when_ps4_cannot_call_back(tmp_path):
    fake, _ = make_console(free_port())  # o payload vai tentar uma porta onde ninguém escuta
    cfg = Config(ps4_ip="127.0.0.1", debug_port=fake.port, callback_port=free_port(), callback_bind="127.0.0.1", workdir=tmp_path)
    session = DebugSession(cfg, PS4Debug("127.0.0.1", fake.port))
    try:
        with pytest.raises(PS4DebugError, match="755"):
            session.attach(PID)
        assert fake.g_debugging  # bug real do payload: fica preso em "debugging"
        with pytest.raises(PS4DebugError, match="já está debugando"):
            session.attach(PID)
        assert not session.attached
    finally:
        fake.stop()
