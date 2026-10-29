"""Tests for the ps4debug fake, written from the C sources' point of view (raw sockets),
plus cross-validation against the independent PyPI ``ps4debug`` client."""

from __future__ import annotations

import asyncio
import socket
import struct
import time

import pytest

from tests.fakes import fake_ps4debug as F
from tests.fakes.fake_ps4debug import FakePS4Debug
from tests.fakes.run_fake import (CODE_BASE, DATA_BASE, GAME_PID, HEAP_BASE, SUBTITLE,
                                  build_demo)


# ---------------------------------------------------------------- helpers
class Raw:
    """Minimal client speaking the wire protocol exactly as protocol.h defines it."""

    def __init__(self, port: int, timeout: float = 3.0):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=timeout)

    def send(self, cmd: int, payload: bytes = b""):
        self.s.sendall(struct.pack("<III", F.PACKET_MAGIC, cmd, len(payload)) + payload)

    def recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise EOFError(f"closed after {len(buf)}/{n} bytes")
            buf += chunk
        return buf

    def u32(self) -> int:
        return struct.unpack("<I", self.recv(4))[0]

    def status(self) -> int:
        return self.u32()

    def closed(self, timeout: float = 2.0) -> bool:
        self.s.settimeout(timeout)
        try:
            return self.s.recv(1) == b""
        except ConnectionResetError:
            return True
        except socket.timeout:
            return False

    def close(self):
        self.s.close()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def dbg_listener():
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)
    ls.settimeout(3)
    yield ls
    ls.close()


@pytest.fixture
def fake(dbg_listener):
    f = FakePS4Debug(port=0, debug_port=dbg_listener.getsockname()[1])
    build_demo(f)
    f.start()
    yield f
    f.stop()


def attach(fake, listener, pid=GAME_PID) -> tuple[Raw, socket.socket]:
    c = Raw(fake.port)
    c.send(F.CMD_DEBUG_ATTACH, struct.pack("<I", pid))
    dbg, _ = listener.accept()
    assert c.status() == F.CMD_SUCCESS
    dbg.settimeout(3)
    return c, dbg


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def parse_interrupt(pkt: bytes) -> dict:
    assert len(pkt) == F.DEBUG_INTERRUPT_PACKET_SIZE
    lwpid, status, tdname = F.INTERRUPT_HEADER.unpack_from(pkt, 0)
    regs = dict(zip(F.REG_NAMES, F.REG64.unpack_from(pkt, 48)))
    dbregs = F.DBREG64.unpack_from(pkt, 48 + 176 + 832)
    return {"lwpid": lwpid, "status": status, "tdname": tdname.split(b"\0")[0].decode(),
            "regs": regs, "dbregs": dbregs, "fp": pkt[224:224 + 832]}


def nothing_pending(sock, wait=0.25) -> bool:
    sock.settimeout(wait)
    try:
        sock.recv(1, socket.MSG_PEEK)
        return False
    except socket.timeout:
        return True
    finally:
        sock.settimeout(3)


# ---------------------------------------------------------------- wire basics
def test_struct_sizes():
    assert F.CMD_PACKET.size == 12
    assert F.PROC_LIST_ENTRY.size == 36
    assert F.PROC_VM_MAP_ENTRY.size == 58
    assert F.PROC_INFO_RESPONSE.size == 188
    assert F.THRINFO_RESPONSE.size == 40
    assert F.REG64.size == 176
    assert F.DBREG64.size == 128
    assert F.INTERRUPT_HEADER.size + F.REG64.size + F.SAVEFPU_YMM_SIZE + F.DBREG64.size == 0x4A0
    assert len(F.default_fpregs()) == 832


def test_version_has_no_status(fake):
    c = Raw(fake.port)
    c.send(F.CMD_VERSION)
    assert c.recv(c.u32()) == b"1.2"


def test_bad_magic_and_short_header_are_ignored_invalid_cmd_disconnects(fake):
    c = Raw(fake.port)
    c.s.sendall(struct.pack("<III", 0xDEADBEEF, F.CMD_VERSION, 0))         # ignored
    c.s.sendall(struct.pack("<III", F.PACKET_MAGIC, F.CMD_VERSION, 0)[:6])   # short header: ignored
    time.sleep(0.15)
    c.s.sendall(b"\0" * 6)                                                   # read as another short header
    time.sleep(0.15)
    c.send(F.CMD_VERSION)
    assert c.recv(c.u32()) == b"1.2"
    c.send(0xBDEE0001)                     # valid 0xBD prefix, unknown group: ignored, no response
    c.send(F.CMD_VERSION)
    assert c.recv(c.u32()) == b"1.2"
    c.send(0x12345678)                     # !VALID_CMD -> return 1 -> disconnect
    assert c.closed()


def test_proc_list_is_newest_first(fake):
    c = Raw(fake.port)
    c.send(F.CMD_PROC_LIST)
    assert c.status() == F.CMD_SUCCESS
    n = c.u32()
    entries = [F.PROC_LIST_ENTRY.unpack(c.recv(36)) for _ in range(n)]
    assert [(name.rstrip(b"\0").decode(), pid) for name, pid in entries][:2] == [("eboot.bin", 87), ("SceAvSetting", 52)]
    assert entries[-1][1] == 0


def test_proc_read_chunks_unmapped_and_page_semantics(fake):
    c = Raw(fake.port)
    rd = lambda pid, a, n: (c.send(F.CMD_PROC_READ, struct.pack("<IQI", pid, a, n)), c.status(), c.recv(n))[2]
    assert rd(GAME_PID, HEAP_BASE + 0x2000, len(SUBTITLE.encode())) == SUBTITLE.encode()
    big = rd(GAME_PID, HEAP_BASE, 20000)                     # 3 chunks of <= 8192
    assert big[0x1000:0x1004] == struct.pack("<I", 100)
    tail = rd(GAME_PID, HEAP_BASE + 0x100000 - 8, 16)      # mapped -> unmapped
    assert tail[8:] == b"\0" * 8
    # proc_rwmem stops at the first bad page: unmapped -> mapped inside one request reads all zeros
    assert rd(GAME_PID, HEAP_BASE - 0x10, 0x2010)[0x10 + 0x1000:0x10 + 0x1004] == b"\0" * 4
    assert rd(4242, 0x1000, 32) == b"\0" * 32                # unknown pid: SUCCESS + zeros


def test_proc_write(fake):
    c = Raw(fake.port)
    data = bytes(range(256)) * 40                            # 10240 bytes -> 2 chunks
    c.send(F.CMD_PROC_WRITE, struct.pack("<IQI", GAME_PID, HEAP_BASE + 0x8000, len(data)))
    assert c.status() == F.CMD_SUCCESS
    c.s.sendall(data)
    assert c.status() == F.CMD_SUCCESS
    assert fake.processes[GAME_PID].read(HEAP_BASE + 0x8000, len(data)) == data
    assert fake.commands[-1] == ("CMD_PROC_WRITE", {"pid": GAME_PID, "address": HEAP_BASE + 0x8000,
                                                    "length": len(data)})


def test_proc_maps_header_quirk_and_unknown_pid(fake):
    c = Raw(fake.port)
    c.send(F.CMD_PROC_MAPS, struct.pack("<I", GAME_PID))
    assert c.status() == F.CMD_SUCCESS
    n = c.u32()
    maps = [F.PROC_VM_MAP_ENTRY.unpack(c.recv(58)) for _ in range(n)]
    real = fake.processes[GAME_PID].mappings
    assert n == len(real)
    assert maps[0] == (b"\0" * 32, F.VM_MIN_USER, F.VM_MAX_USER, 0, 0)     # vm_map header sentinel
    names = [m[0].rstrip(b"\0").decode() for m in maps[1:]]
    assert names[0] == "eboot.bin" and "main stack" not in names          # last real entry dropped
    assert maps[1][1:3] == (CODE_BASE, CODE_BASE + 0x4000) and maps[1][4] == 5
    c.send(F.CMD_PROC_MAPS, struct.pack("<I", 9999))
    assert c.status() == F.CMD_ERROR and c.closed()


def test_proc_info(fake):
    c = Raw(fake.port)
    c.send(F.CMD_PROC_INFO, struct.pack("<I", GAME_PID))
    assert c.status() == F.CMD_SUCCESS
    pid, name, path, tid, cid = F.PROC_INFO_RESPONSE.unpack(c.recv(188))
    assert (pid, name.rstrip(b"\0"), tid.rstrip(b"\0")) == (GAME_PID, b"eboot.bin", b"CUSA12345")
    assert cid.startswith(b"UP9000-CUSA12345_00")


def test_protect_sends_two_statuses(fake):
    c = Raw(fake.port)
    c.send(F.CMD_PROC_PROTECT, struct.pack("<IQII", GAME_PID, HEAP_BASE, 0x4000, 1))
    assert c.status() == F.CMD_SUCCESS
    assert c.status() == F.CMD_DATA_NULL                    # proc.c:298 falls through
    m = fake.processes[GAME_PID]._mapping_at(HEAP_BASE)
    assert (m.start, m.end, m.prot) == (HEAP_BASE, HEAP_BASE + 0x4000, 1)   # entry clipped
    c.send(F.CMD_PROC_WRITE, struct.pack("<IQI", GAME_PID, HEAP_BASE, 4))   # max_prot lost WRITE
    assert c.status() == F.CMD_SUCCESS
    c.s.sendall(b"XXXX")
    assert c.status() == F.CMD_SUCCESS
    assert fake.processes[GAME_PID].read(HEAP_BASE, 4) == b"\0" * 4


def test_alloc_is_first_fit_from_min_offset_and_free(fake):
    c = Raw(fake.port)
    c.send(F.CMD_PROC_ALLOC, struct.pack("<II", GAME_PID, 100))
    assert c.status() == F.CMD_SUCCESS
    addr = struct.unpack("<Q", c.recv(8))[0]
    assert addr == F.VM_MIN_USER
    m = fake.processes[GAME_PID]._mapping_at(addr)
    assert (m.end - m.start, m.prot) == (0x4000, 7)
    c.send(F.CMD_PROC_FREE, struct.pack("<IQI", GAME_PID, addr, 100))
    assert c.status() == F.CMD_SUCCESS
    assert fake.processes[GAME_PID]._mapping_at(addr) is None


def test_scan_reproduces_c_bugs(fake):
    p = fake.add_process(300, "scanme")
    base = 0x10000000
    p.map("scan", base, 0x8000)
    p.map("tail", 0x20000000, 0x4000)                      # last entry: hidden by the maps quirk
    p.write(base + 8, struct.pack("<I", 0x11223344))
    p.write(base + 12, struct.pack("<I", 0xAA223344))       # differs only in the last byte
    p.write(base + 0x4000 + 16, struct.pack("<I", 0x11223344))   # page 1 is never read
    p.write(0x20000000, struct.pack("<I", 0x11223344))      # in the dropped last entry

    def scan(vtype, ctype, values):
        c = Raw(fake.port)
        c.send(F.CMD_PROC_SCAN, struct.pack("<IBBI", 300, vtype, ctype, len(values)))
        assert c.status() == F.CMD_SUCCESS
        c.s.sendall(values)
        assert c.status() == F.CMD_SUCCESS
        out = []
        while (a := struct.unpack("<Q", c.recv(8))[0]) != 2**64 - 1:
            out.append(a)
        return out

    assert scan(4, 0, struct.pack("<I", 0x11223344)) == [base + 8, base + 12, base + 0x4008, base + 0x400C]
    assert scan(0, 0, b"\x44") == []                        # 1-byte exact never matches
    p.write(base + 32, b"\x05")
    assert base + 32 in scan(0, 2, b"\x04")                 # bigger-than works on uint8


def test_debug_command_without_attach_crashes_payload(fake):
    c = Raw(fake.port)
    other = Raw(fake.port)
    c.send(F.CMD_DEBUG_BREAKPT, struct.pack("<IIQ", 0, 1, CODE_BASE))
    assert c.closed() and other.closed()
    assert fake.payload_crashed
    with pytest.raises(OSError):
        Raw(fake.port, timeout=0.5).send(F.CMD_VERSION)


def test_failed_attach_leaves_stale_ctx_errors_not_crash(fake):
    c = Raw(fake.port)
    c.send(F.CMD_DEBUG_ATTACH, struct.pack("<I", 4242))      # PT_ATTACH fails
    assert c.status() == F.CMD_ERROR and c.closed()
    c2 = Raw(fake.port)
    c2.send(F.CMD_DEBUG_BREAKPT, struct.pack("<IIQ", 0, 1, CODE_BASE))
    assert c2.status() == F.CMD_ERROR and c2.closed()        # curdbgctx->pid == 0
    assert not fake.payload_crashed
    c3 = Raw(fake.port)
    c3.send(F.CMD_DEBUG_DETACH)                              # cleanup on the stale ctx
    assert c3.status() == F.CMD_SUCCESS
    c3.send(F.CMD_DEBUG_DETACH)                              # now curdbgcli is NULL -> crash
    assert c3.closed() and fake.payload_crashed


def test_connect_back_failure_leaves_g_debugging_stuck():
    f = FakePS4Debug(port=0, debug_port=free_port(), connect_timeout=1.0)
    build_demo(f)
    f.start()
    try:
        c = Raw(f.port)
        c.send(F.CMD_DEBUG_ATTACH, struct.pack("<I", GAME_PID))
        assert c.status() == F.CMD_ERROR and c.closed()
        assert f.g_debugging and f.attached_pid is None
        c2 = Raw(f.port)
        c2.send(F.CMD_DEBUG_ATTACH, struct.pack("<I", GAME_PID))
        assert c2.status() == F.CMD_ALREADY_DEBUG and c2.closed()
        c3 = Raw(f.port)
        c3.send(F.CMD_DEBUG_DETACH)                          # the only way out
        assert c3.status() == F.CMD_SUCCESS and not f.g_debugging
        assert f.processes[GAME_PID].traced                  # never PT_DETACHed: re-attach fails
        c3.send(F.CMD_DEBUG_ATTACH, struct.pack("<I", GAME_PID))
        assert c3.status() == F.CMD_ERROR
    finally:
        f.stop()


def test_breakpoint_interrupt_layout_and_step_over(fake, dbg_listener):
    c, dbg = attach(fake, dbg_listener)
    assert fake.attached_pid == GAME_PID and fake.process_state == "running"
    addr = CODE_BASE + 4                                     # mov eax,[rip+x] (6 bytes)
    orig = fake.processes[GAME_PID].read(addr, 1)
    c.send(F.CMD_DEBUG_BREAKPT, struct.pack("<IIQ", 0, 1, addr))
    assert c.status() == F.CMD_SUCCESS
    assert fake.breakpoints == {0: (addr, orig[0])}
    c.send(F.CMD_PROC_READ, struct.pack("<IQI", GAME_PID, addr, 1))
    assert c.status() == F.CMD_SUCCESS and c.recv(1) == b"\xcc"
    fake.hit_breakpoint(0, regs={"rax": 0x1234})
    assert fake.process_state == "stopped"
    ev = parse_interrupt(recv_exact(dbg, 0x4A0))
    assert (ev["lwpid"], ev["status"], ev["tdname"]) == (100100, (5 << 8) | 0x7F, "main")
    assert ev["regs"]["rip"] == addr and ev["regs"]["rax"] == 0x1234
    assert (ev["regs"]["trapno"], ev["regs"]["cs"], ev["regs"]["ss"]) == (3, 0x43, 0x3B)
    assert struct.unpack_from("<H", ev["fp"], 0)[0] == 0x37F
    c.send(F.CMD_DEBUG_GETREGS, struct.pack("<I", 100100))   # thread really stepped over it
    assert c.status() == F.CMD_SUCCESS
    regs = dict(zip(F.REG_NAMES, F.REG64.unpack(c.recv(176))))
    assert regs["rip"] == addr + 6
    assert fake.processes[GAME_PID].read(addr, 1) == b"\xcc"  # re-armed
    c.send(F.CMD_DEBUG_STOPGO, struct.pack("<I", 0))
    assert c.status() == F.CMD_SUCCESS and fake.process_state == "running"
    c.send(F.CMD_DEBUG_BREAKPT, struct.pack("<IIQ", 0, 0, 0))
    assert c.status() == F.CMD_SUCCESS
    assert fake.processes[GAME_PID].read(addr, 1) == orig and fake.breakpoints == {}
    c.send(F.CMD_DEBUG_BREAKPT, struct.pack("<IIQ", 30, 1, addr))
    assert c.status() == F.CMD_INVALID_INDEX and c.closed()   # return 1 -> session cleaned up
    assert fake.wait_for(lambda: not fake.g_debugging)


def test_sigstop_sends_no_interrupt(fake, dbg_listener):
    c, dbg = attach(fake, dbg_listener)
    c.send(F.CMD_DEBUG_STOPGO, struct.pack("<I", 1))
    assert c.status() == F.CMD_SUCCESS
    assert fake.process_state == "stopped"
    assert nothing_pending(dbg)
    c.send(F.CMD_DEBUG_STOPGO, struct.pack("<I", 0))
    assert c.status() == F.CMD_SUCCESS and fake.process_state == "running"


def test_watchpoint_dr7_and_hit(fake, dbg_listener):
    c, dbg = attach(fake, dbg_listener)
    c.send(F.CMD_DEBUG_WATCHPT, struct.pack("<IIIIQ", 1, 1, 3, 1, DATA_BASE))  # 4 bytes, write
    assert c.status() == F.CMD_SUCCESS
    expected = F.dr7_set(1, 3, 1, 3)
    assert fake.watchpoints == {1: (DATA_BASE, 3, 1)} and fake.dr7 == expected
    c.send(F.CMD_DEBUG_GETDBGREGS, struct.pack("<I", 100101))
    assert c.status() == F.CMD_SUCCESS
    dr = F.DBREG64.unpack(c.recv(128))
    assert dr[1] == DATA_BASE and dr[7] == expected
    fake.hit_watchpoint(1, lwpid=100101, rip=CODE_BASE + 0x13)
    ev = parse_interrupt(recv_exact(dbg, 0x4A0))
    assert ev["lwpid"] == 100101 and ev["regs"]["rip"] == CODE_BASE + 0x13
    assert ev["dbregs"][6] & (1 << 1) and ev["dbregs"][7] == expected
    c.send(F.CMD_DEBUG_WATCHPT, struct.pack("<IIIIQ", 0, 1, 3, 2, DATA_BASE))  # access 2 -> EINVAL
    assert c.status() == F.CMD_ERROR and c.closed()


def test_singlestep_uses_instruction_length(fake, dbg_listener):
    c, dbg = attach(fake, dbg_listener)
    c.send(F.CMD_DEBUG_SINGLESTEP)
    assert c.status() == F.CMD_ERROR and c.closed()            # not stopped -> PT_STEP fails
    c, dbg = attach(fake, dbg_listener)
    c.send(F.CMD_DEBUG_STOPGO, struct.pack("<I", 1))
    assert c.status() == F.CMD_SUCCESS
    rips = []
    for _ in range(3):
        c.send(F.CMD_DEBUG_SINGLESTEP)
        assert c.status() == F.CMD_SUCCESS
        ev = parse_interrupt(recv_exact(dbg, 0x4A0))
        assert ev["status"] == 0x57F and fake.process_state == "stopped"
        rips.append(ev["regs"]["rip"])
    assert rips == [CODE_BASE + 1, CODE_BASE + 4, CODE_BASE + 10]   # push rbp / mov rbp,rsp / mov eax,[..]


def test_threads_thrinfo_and_setregs_ignores_lwpid(fake, dbg_listener):
    c, _dbg = attach(fake, dbg_listener)
    c.send(F.CMD_DEBUG_THREADS)
    assert c.status() == F.CMD_SUCCESS
    n = struct.unpack("<i", c.recv(4))[0]
    assert struct.unpack(f"<{n}I", c.recv(4 * n)) == (100100, 100101, 100102)
    c.send(F.CMD_DEBUG_THRINFO, struct.pack("<I", 100101))
    assert c.status() == F.CMD_SUCCESS
    lwpid, prio, name = F.THRINFO_RESPONSE.unpack(c.recv(40))
    assert (lwpid, prio, name.rstrip(b"\0")) == (100101, 256, b"RenderThread")
    regs = fake.processes[GAME_PID].threads[100102].pack_regs()
    c.send(F.CMD_DEBUG_SETREGS, struct.pack("<II", 100102, 176))   # lwpid ignored (debug.c:382)
    assert c.status() == F.CMD_SUCCESS
    c.s.sendall(regs)
    assert c.status() == F.CMD_SUCCESS
    assert fake.processes[GAME_PID].threads[100100].regs["rsp"] == \
        fake.processes[GAME_PID].threads[100102].regs["rsp"]
    c.send(F.CMD_DEBUG_STOPTHR, struct.pack("<I", 100102))
    assert c.status() == F.CMD_SUCCESS
    with pytest.raises(RuntimeError):
        fake.hit_breakpoint(0, lwpid=100102)


def test_attached_client_disconnect_cleans_up(fake, dbg_listener):
    c, dbg = attach(fake, dbg_listener)
    addr = CODE_BASE
    c.send(F.CMD_DEBUG_BREAKPT, struct.pack("<IIQ", 3, 1, addr))
    assert c.status() == F.CMD_SUCCESS
    c.close()
    assert fake.wait_for(lambda: not fake.g_debugging)
    assert fake.processes[GAME_PID].read(addr, 1) == b"\x55"
    assert dbg.recv(1) == b""                                   # debug socket closed
    assert not fake.processes[GAME_PID].traced


def test_kill_default_cleans_up_and_server_survives(fake, dbg_listener):
    c, dbg = attach(fake, dbg_listener)
    c.send(F.CMD_DEBUG_STOPGO, struct.pack("<I", 2))
    assert c.status() == F.CMD_SUCCESS
    assert dbg.recv(1) == b""
    assert fake.wait_for(lambda: fake.process_state == "killed")
    assert not fake.g_debugging and fake.attached_pid is None and not fake.payload_crashed
    c2 = Raw(fake.port)
    c2.send(F.CMD_VERSION)
    assert c2.recv(c2.u32()) == b"1.2"


def test_exit_process_default(fake, dbg_listener):
    c, dbg = attach(fake, dbg_listener)
    fake.exit_process()
    assert dbg.recv(1) == b""
    assert fake.wait_for(lambda: fake.process_state == "killed") and fake.alive


def test_exit_process_faithful_null_deref_crash(dbg_listener):
    f = FakePS4Debug(port=0, debug_port=dbg_listener.getsockname()[1], faithful_sigkill=True)
    build_demo(f)
    f.start()
    try:
        c, dbg = attach(f, dbg_listener)
        f.exit_process()
        assert dbg.recv(1) == b""
        assert c.closed() and f.payload_crashed
        assert f.processes[GAME_PID].state == "running"         # SIGKILL swallowed by cleanup
    finally:
        f.stop()


def test_rpc_install_and_call(fake):
    p = fake.processes[GAME_PID]
    p.on_call(0x401000, lambda a, b, *rest: a + b)
    c = Raw(fake.port)
    c.send(F.CMD_PROC_INTALL, struct.pack("<I", GAME_PID))
    assert c.status() == F.CMD_SUCCESS
    stub = struct.unpack("<Q", c.recv(8))[0]
    assert stub == F.VM_MIN_USER and p.read(stub, 5) == b"RSTB\xa3"
    c.send(F.CMD_PROC_CALL, struct.pack("<IQQQQQQQQ", GAME_PID, stub, 0x401000, 40, 2, 0, 0, 0, 0))
    assert c.status() == F.CMD_SUCCESS
    assert struct.unpack("<IQ", c.recv(12)) == (GAME_PID, 42)


def test_notify_print_and_console_info(fake):
    c = Raw(fake.port)
    text = "Olá PS4\0".encode()
    c.send(F.CMD_CONSOLE_NOTIFY, struct.pack("<II", 222, len(text)))
    c.s.sendall(text)                                           # no status before the text
    assert c.status() == F.CMD_SUCCESS
    c.send(F.CMD_CONSOLE_PRINT, struct.pack("<I", 6))
    c.s.sendall(b"hello\0")
    assert c.status() == F.CMD_SUCCESS
    c.send(F.CMD_CONSOLE_INFO)
    assert c.status() == F.CMD_SUCCESS
    assert fake.notifications == ["Olá PS4", "hello"] and fake.notify_log == [(222, "Olá PS4")]
    c.send(F.CMD_CONSOLE_END)
    assert c.closed()


def test_kernel_base_read_and_panic(fake):
    c = Raw(fake.port)
    c.send(F.CMD_KERN_BASE)
    assert c.status() == F.CMD_SUCCESS
    kbase = struct.unpack("<Q", c.recv(8))[0]
    c.send(F.CMD_KERN_READ, struct.pack("<QI", kbase, 4))
    assert c.status() == F.CMD_SUCCESS and c.recv(4) == b"\x7fELF"
    c.send(F.CMD_KERN_READ, struct.pack("<QI", 0xFFFFFFFF00000000, 4))
    assert c.closed() and fake.kernel_panicked and fake.console_rebooted


def test_broadcast_echo(dbg_listener):
    f = FakePS4Debug(port=0, broadcast_port=0).start()
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(2)
        u.sendto(struct.pack("<I", 0x12345678), ("127.0.0.1", f.broadcast_port))
        u.sendto(struct.pack("<I", F.BROADCAST_MAGIC), ("127.0.0.1", f.broadcast_port))
        assert u.recvfrom(16)[0] == struct.pack("<I", F.BROADCAST_MAGIC)
    finally:
        f.stop()


def test_accept_delay():
    f = FakePS4Debug(port=0, accept_delay=0.4).start()
    try:
        time.sleep(0.05)
        t0 = time.monotonic()
        c = Raw(f.port)
        c.send(F.CMD_VERSION)
        assert c.recv(c.u32()) == b"1.2"
        assert time.monotonic() - t0 > 0.2
    finally:
        f.stop()


def test_ninth_client_is_never_served(fake):
    clients = [Raw(fake.port) for _ in range(8)]
    assert fake.wait_for(lambda: fake.connected_clients == 8)
    ninth = Raw(fake.port, timeout=0.5)
    ninth.send(F.CMD_VERSION)
    with pytest.raises(socket.timeout):
        ninth.recv(4)
    for c in clients:
        c.close()


# ---------------------------------------------------- PyPI ps4debug cross-check
ps4lib = pytest.importorskip("ps4debug")


def run(coro):
    return asyncio.run(coro)


def test_pyps4debug_basic_commands(fake):
    from ps4debug import PS4Debug

    async def main():
        ps4 = PS4Debug("127.0.0.1", fake.port, timeout=3)
        try:
            assert await ps4.get_version() == "1.2"
            procs = await ps4.get_processes()
            assert (procs[0].name, procs[0].pid) == ("eboot.bin", GAME_PID)
            maps = await ps4.get_process_maps(GAME_PID)
            assert (maps[0].name, maps[0].start, maps[0].end) == ("", F.VM_MIN_USER, F.VM_MAX_USER)
            assert maps[1].name == "eboot.bin" and maps[1].start == CODE_BASE
            info = await ps4.get_process_info(GAME_PID)
            assert (info.name, info.title_id) == ("eboot.bin", "CUSA12345")
            data = await ps4.read_memory(GAME_PID, HEAP_BASE + 0x3000, len(SUBTITLE) * 2)
            assert data.decode("utf-16-le") == SUBTITLE
            await ps4.write_memory(GAME_PID, HEAP_BASE + 0x9000, b"cross-check")
            assert fake.processes[GAME_PID].read(HEAP_BASE + 0x9000, 11) == b"cross-check"
            await ps4.notify("ação")
            assert fake.notifications[-1] == "ação"
            assert await ps4.get_kernel_base() == fake.kernel_base
            assert await ps4.allocate_memory(GAME_PID, 0x100) == F.VM_MIN_USER
        finally:
            await ps4.pool.close()

    run(main())


def test_pyps4debug_protect_double_status_desyncs_library(fake):
    """The library reads ONE status for PROTECT (like the C# client); the server sends two."""
    from ps4debug import PS4Debug, PS4DebugException, VMProtection

    async def main():
        ps4 = PS4Debug("127.0.0.1", fake.port, timeout=2)
        try:
            await ps4.change_protection(GAME_PID, HEAP_BASE, 0x4000, VMProtection.ALL)
            with pytest.raises(PS4DebugException, match="DATA_NULL"):
                await ps4.read_memory(GAME_PID, HEAP_BASE, 4)    # reads the stale DATA_NULL
            assert await ps4.read_memory(GAME_PID, HEAP_BASE + 0x1000, 4) == struct.pack("<I", 100)
        finally:
            await ps4.pool.close()

    run(main())


def test_pyps4debug_struct_layout_discrepancies():
    """Library bugs vs debug.h: __reg64 fs/gs/es/ds are uint16 (176 bytes), not uint64."""
    from ps4debug.core import DebuggerInterrupt, Registers64, ThreadInfo
    assert Registers64.struct.sizeof() == 200 != F.REG64.size
    assert DebuggerInterrupt.struct.sizeof() == 1208 != F.DEBUG_INTERRUPT_PACKET_SIZE
    assert ThreadInfo.struct.sizeof() == F.THRINFO_RESPONSE.size   # size ok, name decoded as UTF-16


def _fixed_interrupt_model():
    from typing import Annotated
    from construct import Int16ul, Int32ul, Int64ul
    from pydantic_construct import ConstructModel
    from ps4debug.core import DebugRegisters, FPRegisters
    from ps4debug.core.base import ascii_string

    q, i, h = Int64ul, Int32ul, Int16ul
    ns = {"__annotations__": {}}
    for name, t in zip(F.REG_NAMES, [q] * 15 + [i, h, h, i, h, h] + [q] * 5):
        ns["__annotations__"][name] = Annotated[int, t]
    Regs = type("FixedRegisters64", (ConstructModel,), ns)

    class FixedDebuggerInterrupt(ConstructModel):
        lwpid: Annotated[int, Int32ul]
        status: Annotated[int, Int32ul]
        name: Annotated[str, ascii_string(length=40, strip=True)]
        regs: Regs
        fp_regs: FPRegisters
        db_regs: DebugRegisters

    assert FixedDebuggerInterrupt.struct.sizeof() == 0x4A0
    return FixedDebuggerInterrupt


def test_pyps4debug_attach_breakpoint_callback(fake, monkeypatch):
    """Attach + breakpoint through the library's own debug server/callback, with the interrupt
    model corrected to debug.h's layout (the shipped one is 1208 bytes and never fires)."""
    import ps4debug.debugging.context as ctxmod
    from ps4debug import PS4Debug

    monkeypatch.setattr(ctxmod, "DebuggerInterrupt", _fixed_interrupt_model())
    port = free_port()
    fake.debug_port = port
    addr = CODE_BASE + 4

    async def main():
        ps4 = PS4Debug("127.0.0.1", fake.port, timeout=3)
        hits = []
        got = asyncio.Event()
        try:
            async with ps4.debugger(GAME_PID, port=port) as dbg:
                async def cb(ev):
                    hits.append(ev)
                    got.set()
                dbg.register_callback(cb)
                idx = await dbg.add_breakpoint(addr)
                assert fake.breakpoints == {idx: (addr, 0x8B)}
                await asyncio.to_thread(fake.hit_breakpoint, idx)
                await asyncio.wait_for(got.wait(), 3)
                assert await asyncio.to_thread(fake.wait_for, lambda: fake.process_state == "running")
        finally:
            await ps4.pool.close()
        return hits

    hits = run(main())
    ev = hits[0].interrupt
    assert (ev.lwpid, ev.status, ev.name, ev.regs.rip) == (100100, 0x57F, "main", addr)
    assert fake.wait_for(lambda: not fake.g_debugging)
    assert fake.processes[GAME_PID].read(addr, 1) == b"\x8b"
