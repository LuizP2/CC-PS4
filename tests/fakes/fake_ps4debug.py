"""Protocol-faithful fake of the ps4debug 1.2 payload server (jogolden/ps4debug).

Behaviour is derived from the real C sources only.  Citations below are
``file:line`` relative to the ps4debug repository:

* debugger/include/{protocol.h,debug.h,proc.h,kdbg.h,net.h,server.h}
* debugger/source/{server.c,debug.c,proc.c,kern.c,console.c,net.c}
* kdebugger/source/{hooks.c,proc.c}  (the custom syscalls 107-112 that the
  payload calls; they define read/write/maps/alloc semantics)
* installer/source/installer.c       (kernel patches + where the payload runs)

Kernel semantics that the C code relies on (ptrace/wait4/vm_map) follow
FreeBSD 9, which Orbis OS is based on. The relevant assumptions are marked
"[FreeBSD]" in comments.

Quick map of emulated quirks (details at each handler):

* CMD_VERSION has no status (server.c:31-36).
* Handlers returning 1 close the connection (server.c:284-292); closing the
  attached client runs debug_cleanup (server.c:20-29).
* Packets with a bad magic, or a header that arrives short, are silently
  ignored (server.c:235-245). Unknown 0xBD.. groups are ignored; anything
  else disconnects (server.c:38-60).
* Debug commands other than ATTACH while ``curdbgctx`` is NULL dereference
  NULL. That crashes the payload (and SceShellCore, which hosts it,
  installer.c load_debugger).
* ``curdbgctx`` is set when ANY ATTACH arrives while not debugging
  (server.c:271-274), even if the attach then fails. Later debug commands
  then see pid 0 -> CMD_ERROR + close instead of crashing, and DETACH resets
  the state.
* A failed connect-back leaves ``g_debugging`` stuck at 1 (debug.c:544).
* PROC_PROTECT sends TWO statuses: SUCCESS then DATA_NULL (proc.c:289-298).
* PROC_MAPS: the kdebugger starts walking at vm_map_lookup_entry(map, NULL),
  which yields the map *header* sentinel. Entry 0 is therefore
  {"" , min_offset, max_offset, 0, prot 0} and the LAST real entry is
  dropped (kdebugger hooks.c:103-121). Toggle with ``maps_header_quirk``.
* PROC_SCAN bugs (proc.c:633-719), see ``_proc_scan``.
* PROC_READ/WRITE use FreeBSD proc_rwmem: page by page, stopping at the
  first unreadable page of each request (kdebugger proc.c:89-127).
* The SIGKILL branch of check_debug_interrupt dereferences curdbgctx after
  debug_cleanup() has set it to NULL (server.c:82-86, debug.c:578). That is a
  crash on real hardware. It is emulated only with
  ``faithful_sigkill=True``. The default follows the intended behaviour
  (process killed, session cleaned up, debug socket closed).
"""

from __future__ import annotations

import errno as _errno
import select
import socket
import struct
import threading
import time
from typing import Callable

try:  # instruction lengths for single-step / breakpoint step-over
    import capstone as _capstone

    _CS = _capstone.Cs(_capstone.CS_ARCH_X86, _capstone.CS_MODE_64)
except Exception:  # pragma: no cover - capstone is a project dependency
    _CS = None

# --------------------------------------------------------------------------
# protocol.h constants
# --------------------------------------------------------------------------
PACKET_VERSION = "1.2"                       # protocol.h:12
PACKET_MAGIC = 0xFFAABBCC                    # protocol.h:13
CMD_PACKET_SIZE = 12                         # protocol.h:79

CMD_VERSION = 0xBD000001
CMD_PROC_LIST = 0xBDAA0001
CMD_PROC_READ = 0xBDAA0002
CMD_PROC_WRITE = 0xBDAA0003
CMD_PROC_MAPS = 0xBDAA0004
CMD_PROC_INTALL = 0xBDAA0005
CMD_PROC_CALL = 0xBDAA0006
CMD_PROC_ELF = 0xBDAA0007
CMD_PROC_PROTECT = 0xBDAA0008
CMD_PROC_SCAN = 0xBDAA0009
CMD_PROC_INFO = 0xBDAA000A
CMD_PROC_ALLOC = 0xBDAA000B
CMD_PROC_FREE = 0xBDAA000C
CMD_DEBUG_ATTACH = 0xBDBB0001
CMD_DEBUG_DETACH = 0xBDBB0002
CMD_DEBUG_BREAKPT = 0xBDBB0003
CMD_DEBUG_WATCHPT = 0xBDBB0004
CMD_DEBUG_THREADS = 0xBDBB0005
CMD_DEBUG_STOPTHR = 0xBDBB0006
CMD_DEBUG_RESUMETHR = 0xBDBB0007
CMD_DEBUG_GETREGS = 0xBDBB0008
CMD_DEBUG_SETREGS = 0xBDBB0009
CMD_DEBUG_GETFPREGS = 0xBDBB000A
CMD_DEBUG_SETFPREGS = 0xBDBB000B
CMD_DEBUG_GETDBGREGS = 0xBDBB000C
CMD_DEBUG_SETDBGREGS = 0xBDBB000D
CMD_DEBUG_STOPGO = 0xBDBB0010
CMD_DEBUG_THRINFO = 0xBDBB0011
CMD_DEBUG_SINGLESTEP = 0xBDBB0012
CMD_KERN_BASE = 0xBDCC0001
CMD_KERN_READ = 0xBDCC0002
CMD_KERN_WRITE = 0xBDCC0003
CMD_CONSOLE_REBOOT = 0xBDDD0001
CMD_CONSOLE_END = 0xBDDD0002
CMD_CONSOLE_PRINT = 0xBDDD0003
CMD_CONSOLE_NOTIFY = 0xBDDD0004
CMD_CONSOLE_INFO = 0xBDDD0005

CMD_SUCCESS = 0x80000000                     # protocol.h:63-68
CMD_ERROR = 0xF0000001
CMD_TOO_MUCH_DATA = 0xF0000002
CMD_DATA_NULL = 0xF0000003
CMD_ALREADY_DEBUG = 0xF0000004
CMD_INVALID_INDEX = 0xF0000005

MAX_BREAKPOINTS = 30                         # protocol.h:300
MAX_WATCHPOINTS = 4                          # protocol.h:301
NET_MAX_LENGTH = 8192                        # net.h:11
SERVER_MAXCLIENTS = 8                        # server.h:18
BROADCAST_MAGIC = 0xFFFFAAAA                 # server.h:21
DEBUG_PORT = 755                             # debug.h:142
SERVER_PORT = 744                            # server.h:17

PAGE_SIZE = 0x4000      # kdebugger/include/proc.h:12 (PS4 uses 16 KiB pages)
# [FreeBSD] vm_map min/max for user processes (min = one page because
# map_at_zero=0; max = amd64 VM_MAXUSER_ADDRESS).
VM_MIN_USER = 0x4000
VM_MAX_USER = 0x800000000000

# Packed wire structures (sizes asserted in tests)
CMD_PACKET = struct.Struct("<III")                        # 12
PROC_LIST_ENTRY = struct.Struct("<32si")                  # kdbg.h:16-19 -> 36
PROC_VM_MAP_ENTRY = struct.Struct("<32sQQQH")             # proc.h:13-19 -> 58
PROC_INFO_RESPONSE = struct.Struct("<I40s64s16s64s")      # protocol.h:183-191 -> 188
THRINFO_RESPONSE = struct.Struct("<II32s")                # protocol.h:261-268 -> 40
REG64 = struct.Struct("<15QIHHIHH5Q")                     # debug.h:13-40 -> 176
DBREG64 = struct.Struct("<16Q")                           # debug.h:101-108 -> 128
SAVEFPU_YMM_SIZE = 832                                    # debug.h:90-99
INTERRUPT_HEADER = struct.Struct("<II40s")                # debug.h:110-117
DEBUG_INTERRUPT_PACKET_SIZE = 0x4A0                       # debug.h:118

REG_NAMES = (
    "r15", "r14", "r13", "r12", "r11", "r10", "r9", "r8", "rdi", "rsi", "rbp",
    "rbx", "rdx", "rcx", "rax", "trapno", "fs", "gs", "err", "es", "ds", "rip",
    "cs", "rflags", "rsp", "ss",
)
# [FreeBSD amd64] user selectors: GUCODE_SEL=8 -> 0x43, GUDATA_SEL=7 -> 0x3b
_REG_MASK = {k: (1 << (8 * struct.calcsize(c))) - 1 for k, c in zip(REG_NAMES, "Q" * 15 + "IHHIHH" + "Q" * 5)}
REG_DEFAULTS = {"cs": 0x43, "ss": 0x3B, "rflags": 0x202}

SIGTRAP, SIGKILL, SIGSEGV, SIGSTOP = 5, 9, 11, 17           # ptrace.h:61-92
# [FreeBSD amd64] trap numbers reported in r_trapno
T_PRIVINFLT, T_BPTFLT, T_PROTFLT, T_TRCTRAP, T_PAGEFLT, T_DIVIDE = 1, 3, 9, 10, 12, 18
_SIG_TRAPNO = {SIGSEGV: T_PAGEFLT, 10: T_PROTFLT, 4: T_PRIVINFLT, 8: T_DIVIDE, SIGTRAP: T_TRCTRAP}
# AMD64 DR6 reserved bits read as 1 (bits 4-11, 16-31); a real hit reads 0xFFFF0FF{1,2,4,8}
DR6_RESERVED_ONES = 0xFFFF0FF0

MASK64 = (1 << 64) - 1
MALLOC_LIMIT = 256 << 20   # pfmalloc() failure threshold (assumption)

# kdebugger/include/rpcasm.h:40 - the real 410-byte RPC stub (magic 'RSTB', entry 0xA3)
RPCSTUB = bytes.fromhex(
    "52535442a3000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000006c69626b65726e656c2e73707278006c6962"
    "6b65726e656c5f7765622e73707278006c69626b65726e656c5f7379732e7370"
    "72780000000000000000007363654b65726e656c55736c656570000000000000"
    "000000b900000000488d15d4ffffffbe00000000488d3d93ffffffe8c4000000"
    "4885c0743fb900000000488d15b2ffffffbe00000000488d3d80ffffffe8a200"
    "00004885c0741db900000000488d1590ffffffbe00000000488d3d71ffffffe8"
    "80000000488d1590ffffff488d3579ffffff488b3d6affffffe871000000803d"
    "27ffffff0074494c8b0d0effffff4c8b05fffeffff488b0df0feffff488b15e1"
    "feffff488b35d2feffff488b3dc3feffff4c8b25b4feffff41ffd4488905e2fe"
    "ffffc605e3feffff00c605ddfeffff01bfa08601004c8b251fffffff41ffd4eb"
    "9d31c0c3b8520200004989ca0f05c3b84f0200004989ca0f05c3"
)
assert len(RPCSTUB) == 410
RPCSTUB_RIP_OFF, RPCSTUB_RAX_OFF, RPCSTUB_GO_OFF, RPCSTUB_DONE_OFF = 12, 68, 76, 77

CMD_NAMES = {v: k for k, v in globals().items() if k.startswith("CMD_") and isinstance(v, int)
             and (v >> 24) == 0xBD}

# field layouts for the command log
_FIELDS = {
    CMD_PROC_READ: ("<IQI", ("pid", "address", "length")),
    CMD_PROC_WRITE: ("<IQI", ("pid", "address", "length")),
    CMD_PROC_MAPS: ("<I", ("pid",)),
    CMD_PROC_INTALL: ("<I", ("pid",)),
    CMD_PROC_CALL: ("<IQQQQQQQQ", ("pid", "rpcstub", "rip", "rdi", "rsi", "rdx", "rcx", "r8", "r9")),
    CMD_PROC_ELF: ("<II", ("pid", "length")),
    CMD_PROC_PROTECT: ("<IQII", ("pid", "address", "length", "newprot")),
    CMD_PROC_SCAN: ("<IBBI", ("pid", "valueType", "compareType", "lenData")),
    CMD_PROC_INFO: ("<I", ("pid",)),
    CMD_PROC_ALLOC: ("<II", ("pid", "length")),
    CMD_PROC_FREE: ("<IQI", ("pid", "address", "length")),
    CMD_DEBUG_ATTACH: ("<I", ("pid",)),
    CMD_DEBUG_BREAKPT: ("<IIQ", ("index", "enabled", "address")),
    CMD_DEBUG_WATCHPT: ("<IIIIQ", ("index", "enabled", "length", "breaktype", "address")),
    CMD_DEBUG_STOPTHR: ("<I", ("lwpid",)),
    CMD_DEBUG_RESUMETHR: ("<I", ("lwpid",)),
    CMD_DEBUG_GETREGS: ("<I", ("lwpid",)),
    CMD_DEBUG_GETFPREGS: ("<I", ("lwpid",)),
    CMD_DEBUG_GETDBGREGS: ("<I", ("lwpid",)),
    CMD_DEBUG_SETREGS: ("<II", ("lwpid", "length")),
    CMD_DEBUG_SETFPREGS: ("<II", ("lwpid", "length")),
    CMD_DEBUG_SETDBGREGS: ("<II", ("lwpid", "length")),
    CMD_DEBUG_STOPGO: ("<I", ("stop",)),
    CMD_DEBUG_THRINFO: ("<I", ("lwpid",)),
    CMD_KERN_READ: ("<QI", ("address", "length")),
    CMD_KERN_WRITE: ("<QI", ("address", "length")),
    CMD_CONSOLE_PRINT: ("<I", ("length",)),
    CMD_CONSOLE_NOTIFY: ("<II", ("messageType", "length")),
}


def _unpack(fmt: str, data: bytes | None) -> tuple:
    """Unpack a packet struct; short packets are zero padded (real: reads past the buffer)."""
    size = struct.calcsize(fmt)
    data = (data or b"")[:size].ljust(size, b"\0")
    return struct.unpack(fmt, data)


def page_trunc(a: int) -> int:
    return a & ~(PAGE_SIZE - 1)


def page_round(a: int) -> int:
    return (a + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1)


def dr7_mask(i: int) -> int:     # debug.h:133
    return ((0xF << (i * 4 + 16)) | (0x3 << (i * 2))) & MASK64


def dr7_set(i: int, length: int, access: int, enable: int) -> int:   # debug.h:134
    return (((((length << 2) | access) & 0xFFFFFFFF) << (i * 4 + 16)) | (enable << (i * 2))) & MASK64


def dr7_enabled(d: int, i: int) -> bool:   # debug.h:136
    return (d & (0x3 << (i * 2))) != 0


def dr7_access(d: int, i: int) -> int:     # debug.h:137
    return (d >> (i * 4 + 16)) & 0x3


def dr7_len(d: int, i: int) -> int:        # debug.h:138
    return (d >> (i * 4 + 18)) & 0x3


def wait_status_stopped(sig: int) -> int:
    """FreeBSD wait status of a stopped process: WSTOPSIG in bits 8-15, 0x7f low byte."""
    return ((sig & 0xFF) << 8) | 0x7F


def insn_length(code: bytes, address: int = 0) -> int:
    if _CS is not None:
        for insn in _CS.disasm(bytes(code), address, count=1):
            return insn.size
    return 1


def default_fpregs() -> bytearray:
    """savefpu_ymm with FreeBSD's initial FPU state (cw 0x37f, mxcsr 0x1f80)."""
    fp = bytearray(SAVEFPU_YMM_SIZE)
    struct.pack_into("<H", fp, 0, 0x037F)
    struct.pack_into("<II", fp, 24, 0x1F80, 0xFFFF)
    return fp


class _Crash(Exception):
    """The real payload would fault here (NULL deref / smashed stack)."""


class _Hang(Exception):
    """The real handler thread spins forever. The connection is never served again."""


class _ConnLost(Exception):
    pass


# --------------------------------------------------------------------------
# process model
# --------------------------------------------------------------------------
class FakeThread:
    def __init__(self, lwpid: int, name: str, priority: int, regs: dict | None, rsp: int):
        self.lwpid = lwpid
        self.name = name
        self.priority = priority
        self.regs = {k: 0 for k in REG_NAMES}
        self.regs.update(REG_DEFAULTS)
        self.regs["rsp"] = rsp
        if regs:
            self.regs.update(_check_regs(regs))
        self.fpregs = default_fpregs()
        self.dbregs = [0] * 16
        self.suspended = False

    def pack_regs(self, regs: dict | None = None) -> bytes:
        r = regs or self.regs
        return REG64.pack(*[r[k] & _REG_MASK[k] for k in REG_NAMES])


def _check_regs(regs: dict) -> dict:
    bad = set(regs) - set(REG_NAMES)
    if bad:
        raise ValueError(f"unknown register names: {sorted(bad)}")
    return dict(regs)


class _Mapping:
    __slots__ = ("name", "start", "end", "prot", "max_prot", "offset", "data")

    def __init__(self, name, start, end, prot, offset=0, data=None, max_prot=7):
        self.name, self.start, self.end = name, start, end
        self.prot, self.max_prot, self.offset = prot, max_prot, offset
        self.data = data if data is not None else bytearray(end - start)


class FakeProcess:
    """A process of the fake console. Created by FakePS4Debug.add_process()."""

    def __init__(self, server: "FakePS4Debug", pid: int, name: str, titleid: str = "",
                 contentid: str = "", path: str = ""):
        self._srv = server
        self.pid, self.name, self.titleid, self.contentid, self.path = pid, name, titleid, contentid, path
        self.mappings: list[_Mapping] = []
        self.threads: dict[int, FakeThread] = {}
        self.call_handlers: dict[int, Callable[..., int]] = {}
        self.code_handlers: list[tuple[bytes, Callable[..., int]]] = []
        self.rpc_stubs: dict[int, int | None] = {}   # stub address -> stub thread lwpid (None: no thread)
        self.loaded_elfs: list[tuple[int, bytes]] = []
        # ptrace / wait4 state
        self.state = "running"          # running | stopped | killed
        self.traced = False
        self.xthread: int | None = None   # p_xthread: thread that caused the trace stop
        self.wait_pending: int | None = None
        self.reaped = False
        self.pending_sigkill = False

    # ---- public helpers (test API) ----
    def map(self, name: str, start: int, data_or_size: bytes | int, prot: int = 3, offset: int = 0) -> None:
        """Add a vm_map entry. Bounds are rounded to 16 KiB pages (vm_map entries are page aligned);
        the data is placed at ``start`` and the rest is zero filled."""
        data = b"" if isinstance(data_or_size, int) else bytes(data_or_size)
        size = data_or_size if isinstance(data_or_size, int) else len(data)
        s, e = page_trunc(start), page_round(start + max(size, 1))
        with self._srv._lock:
            for m in self.mappings:
                if m.start < e and s < m.end:
                    raise ValueError(f"mapping {name!r} [{s:#x},{e:#x}) overlaps {m.name!r} [{m.start:#x},{m.end:#x})")
            m = _Mapping(name, s, e, prot, offset)
            m.data[start - s:start - s + len(data)] = data
            self._insert(m)

    def load_elf(self, path: str, base: int, name: str = "eboot.bin") -> None:
        """Map each PT_LOAD segment at base+p_vaddr (page aligned), prot from p_flags, file bytes +
        zero fill. Segments sharing a 16 KiB page are merged into one entry (prot OR-ed)."""
        blob = open(path, "rb").read()
        if blob[:4] != b"\x7fELF" or blob[4] != 2:
            raise ValueError(f"{path} is not an ELF64 file (PS4 SELFs must be decrypted first)")
        phoff, = struct.unpack_from("<Q", blob, 0x20)
        phentsize, phnum = struct.unpack_from("<HH", blob, 0x36)
        segs = []
        for i in range(phnum):
            p_type, p_flags, p_off, p_vaddr, _pa, p_filesz, p_memsz, _al = struct.unpack_from(
                "<IIQQQQQQ", blob, phoff + i * phentsize)
            if p_type != 1 or p_memsz == 0:
                continue
            prot = (1 if p_flags & 4 else 0) | (2 if p_flags & 2 else 0) | (4 if p_flags & 1 else 0)
            segs.append((base + p_vaddr, p_memsz, prot, blob[p_off:p_off + p_filesz], p_off))
        if not segs:
            raise ValueError(f"{path} has no PT_LOAD segments")
        ranges: list[list] = []
        for va, memsz, prot, _d, off in sorted(segs):
            s, e = page_trunc(va), page_round(va + memsz)
            if ranges and s < ranges[-1][1]:
                ranges[-1][1] = max(ranges[-1][1], e)
                ranges[-1][2] |= prot
            else:
                ranges.append([s, e, prot, off])
        for s, e, prot, off in ranges:
            self.map(name, s, e - s, prot=prot, offset=off & ~(PAGE_SIZE - 1))
        for va, _m, _p, data, _o in segs:
            self.write(va, data)

    def add_thread(self, lwpid: int, name: str, priority: int = 700, regs: dict | None = None) -> None:
        with self._srv._lock:
            if lwpid in self.threads:
                raise ValueError(f"thread {lwpid} already exists")
            rsp = 0x7EFFFFFFC000 - 0x100000 * len(self.threads)
            self.threads[lwpid] = FakeThread(lwpid, name, priority, regs, rsp)

    def on_call(self, address: int, fn: Callable[..., int]) -> None:
        """Python implementation of the function at ``address`` for CMD_PROC_CALL (rdi..r9 -> rax)."""
        self.call_handlers[address] = fn

    def on_call_code(self, code: bytes, fn: Callable[..., int]) -> None:
        """Como on_call, mas para código que o cliente aloca e copia: casa pelos bytes em rip."""
        self.code_handlers.append((code, fn))

    def read(self, address: int, size: int) -> bytes:
        """Raw memory (ignores protections); unmapped bytes read as zero."""
        out = bytearray(size)
        with self._srv._lock:
            for m in self.mappings:
                lo, hi = max(address, m.start), min(address + size, m.end)
                if lo < hi:
                    out[lo - address:hi - address] = m.data[lo - m.start:hi - m.start]
        return bytes(out)

    def write(self, address: int, data: bytes) -> None:
        """Raw write (ignores protections). Raises ValueError if any byte is unmapped."""
        with self._srv._lock:
            covered = 0
            for m in self.mappings:
                lo, hi = max(address, m.start), min(address + len(data), m.end)
                if lo < hi:
                    m.data[lo - m.start:hi - m.start] = data[lo - address:hi - address]
                    covered += hi - lo
            if covered != len(data):
                raise ValueError(f"write to unmapped memory at {address:#x} (+{len(data):#x})")

    # ---- internals ----
    @property
    def alive(self) -> bool:
        return self.state != "killed"

    def _insert(self, m: _Mapping) -> None:
        self.mappings.append(m)
        self.mappings.sort(key=lambda x: x.start)

    def _mapping_at(self, addr: int) -> _Mapping | None:
        for m in self.mappings:
            if m.start <= addr < m.end:
                return m
        return None

    def _first_thread(self) -> FakeThread | None:
        return next(iter(self.threads.values()), None)

    def _clip(self, start: int, end: int) -> list[_Mapping]:
        """vm_map_clip_start/end: split entries at start/end and return the ones inside."""
        out = []
        for m in list(self.mappings):
            if m.end <= start or m.start >= end:
                continue
            for cut in (start, end):
                if m.start < cut < m.end:
                    k = cut - m.start
                    tail = _Mapping(m.name, cut, m.end, m.prot, m.offset + k, m.data[k:], m.max_prot)
                    m.data = m.data[:k]
                    m.end = cut
                    self._insert(tail)
        for m in self.mappings:
            if start <= m.start and m.end <= end:
                out.append(m)
        return out

    def _trace_stop(self, thread: FakeThread | None, sig: int) -> None:
        """[FreeBSD] ptracestop(): process stops, wait4 will report WSTOPSIG == sig."""
        self.state = "stopped"
        self.xthread = thread.lwpid if thread else None
        self.wait_pending = wait_status_stopped(sig)

    def _resume(self) -> None:
        """[FreeBSD] PT_CONTINUE on a stopped process clears P_STOPPED/P_WAITED: an uncollected
        stop is never reported. A SIGKILL queued while stopped is processed right away (traced
        -> stops again with SIGKILL)."""
        self.state = "running"
        self.xthread = None
        self.wait_pending = None
        if self.pending_sigkill:
            self.pending_sigkill = False
            self._trace_stop(self._first_thread(), SIGKILL)

    def _wait4(self) -> tuple[int, int | None]:
        """wait4(pid, WNOHANG): (0, None) nothing; (pid, status); (-1, None) ECHILD."""
        if self.reaped:
            return -1, None
        if self.wait_pending is None:
            return 0, None
        status, self.wait_pending = self.wait_pending, None
        if self.state == "killed":
            self.reaped = True
        return self.pid, status


class _DebugContext:
    """struct debug_context (protocol.h:316-324). Lives inside a client slot."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.pid = 0
        self.dbgfd: socket.socket | None = None
        self.breakpoints = [[0, 0, 0] for _ in range(MAX_BREAKPOINTS)]  # enabled, address, original
        self.watchdata = [0] * 16
        self.peer_fin_sends = 0


class _Slot:
    """struct server_client (protocol.h:326-332): one of SERVER_MAXCLIENTS static slots."""

    def __init__(self, index: int):
        self.index = index
        self.dbgctx = _DebugContext()
        self.reset()

    def reset(self):          # memset(svc, 0) in free_client (server.c:28); dbgctx keeps its identity
        self.id = 0
        self.sock: socket.socket | None = None
        self.addr: tuple | None = None
        self.debugging = False
        self.dbgctx.reset()


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------
class FakePS4Debug:
    """Fake ps4debug payload. ``start()`` binds; ``self.port`` holds the real TCP port."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, debug_port: int = DEBUG_PORT,
                 broadcast_port: int | None = None, version: str = PACKET_VERSION,
                 accept_delay: float = 0.0, *, kernel_base: int = 0xFFFFFFFF82200000,
                 kernel_size: int = 0x200000, faithful_sigkill: bool = False,
                 maps_header_quirk: bool = True, poll_interval: float = 0.005,
                 connect_timeout: float = 3.0):
        self.host, self.port, self.debug_port = host, port, debug_port
        self.broadcast_port = broadcast_port
        self.version = version
        self.accept_delay = accept_delay
        self.faithful_sigkill = faithful_sigkill
        self.maps_header_quirk = maps_header_quirk
        self.poll_interval = poll_interval
        self.connect_timeout = connect_timeout
        self.kernel_base = kernel_base
        self.kernel_memory = bytearray(kernel_size)
        self.kernel_memory[:8] = b"\x7fELF\x02\x01\x01\x09"   # kernel image starts with its ELF header

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.processes: dict[int, FakeProcess] = {}
        self._slots = [_Slot(i) for i in range(SERVER_MAXCLIENTS)]
        self.g_debugging = False
        self._curdbgcli: _Slot | None = None
        self._curdbgctx: _DebugContext | None = None
        self._last_debug_pid: int | None = None

        self.payload_crashed = False
        self.crash_reason: str | None = None
        self.console_rebooted = False
        self.kernel_panicked = False
        self.commands: list[tuple[str, dict]] = []
        self.notifications: list[str] = []
        self.notify_log: list[tuple[int, str]] = []
        self.print_log: list[str] = []
        self.interrupts: list[dict] = []

        self._listener: socket.socket | None = None
        self._udp: socket.socket | None = None
        self._orphans: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stop_evt = threading.Event()
        self._dead = False

    # ------------------------------------------------------------------ setup
    def add_process(self, pid: int, name: str, titleid: str = "", contentid: str = "",
                    path: str = "") -> FakeProcess:
        with self._lock:
            if pid in self.processes:
                raise ValueError(f"pid {pid} already exists")
            p = FakeProcess(self, pid, name, titleid, contentid, path)
            self.processes[pid] = p
            return p

    def start(self) -> "FakePS4Debug":
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        ls.listen(SERVER_MAXCLIENTS * 2)               # server.c:407
        self.port = ls.getsockname()[1]
        self._listener = ls
        self._spawn(self._accept_loop, "fake-ps4debug-accept")
        if self.broadcast_port is not None:
            us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            us.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            us.bind((self.host, self.broadcast_port))
            self.broadcast_port = us.getsockname()[1]
            self._udp = us
            self._spawn(self._broadcast_loop, "fake-ps4debug-broadcast")
        return self

    def stop(self) -> None:
        self._stop_evt.set()
        self._shutdown_sockets()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=2.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------- properties
    @property
    def alive(self) -> bool:
        return not (self._dead or self.payload_crashed or self.console_rebooted)

    @property
    def attached_pid(self) -> int | None:
        with self._lock:
            cli, ctx = self._curdbgcli, self._curdbgctx
            if cli is not None and cli.debugging and ctx is not None and ctx.pid:
                return ctx.pid
            return None

    @property
    def breakpoints(self) -> dict[int, tuple[int, int]]:
        with self._lock:
            ctx = self._curdbgctx
            if ctx is None:
                return {}
            return {i: (b[1], b[2]) for i, b in enumerate(ctx.breakpoints) if b[0]}

    @property
    def watchpoints(self) -> dict[int, tuple[int, int, int]]:
        with self._lock:
            ctx = self._curdbgctx
            if ctx is None:
                return {}
            d7 = ctx.watchdata[7]
            return {i: (ctx.watchdata[i], dr7_len(d7, i), dr7_access(d7, i))
                    for i in range(MAX_WATCHPOINTS) if dr7_enabled(d7, i)}

    @property
    def dr7(self) -> int:
        with self._lock:
            return self._curdbgctx.watchdata[7] if self._curdbgctx else 0

    @property
    def process_state(self) -> str | None:
        """State of the attached process (or of the last one attached; None if never)."""
        with self._lock:
            pid = self.attached_pid or self._last_debug_pid
            p = self.processes.get(pid) if pid is not None else None
            return p.state if p else None

    @property
    def connected_clients(self) -> int:
        with self._lock:
            return sum(1 for s in self._slots if s.id)

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    # -------------------------------------------------------- event injection
    def _attached_process(self) -> tuple[_DebugContext, FakeProcess]:
        if not self.alive:
            raise RuntimeError("payload is not running")
        pid = self.attached_pid
        if pid is None:
            raise RuntimeError("no debugger attached (events are delivered through the attached client)")
        p = self.processes.get(pid)
        if p is None or not p.alive:
            raise RuntimeError(f"attached process {pid} is dead")
        return self._curdbgctx, p

    def _event_thread(self, p: FakeProcess, lwpid: int | None) -> FakeThread:
        if p.state != "running":
            raise RuntimeError(f"process {p.pid} is {p.state}; a stopped process cannot trap "
                               "(continue it first with STOPGO 0)")
        th = p.threads.get(lwpid) if lwpid is not None else p._first_thread()
        if th is None:
            raise RuntimeError(f"no thread {lwpid!r} in process {p.pid}")
        if th.suspended:
            raise RuntimeError(f"thread {th.lwpid} is suspended (STOPTHR) and cannot trap")
        return th

    def hit_breakpoint(self, index: int, lwpid: int | None = None, regs: dict | None = None) -> None:
        """The thread executes the int3 planted by CMD_DEBUG_BREAKPT ``index``."""
        with self._lock:
            ctx, p = self._attached_process()
            enabled, addr, _orig = ctx.breakpoints[index]
            if not enabled:
                raise RuntimeError(f"breakpoint {index} is not enabled")
            if p.read(addr, 1) != b"\xcc":
                raise RuntimeError(f"no int3 at {addr:#x} (overwritten?) - the CPU would not trap")
            th = self._event_thread(p, lwpid)
            if regs:
                th.regs.update(_check_regs(regs))
            th.regs["rip"] = (addr + 1) & MASK64          # #BP leaves rip after the int3
            if not regs or "trapno" not in regs:
                th.regs["trapno"] = T_BPTFLT
            p._trace_stop(th, SIGTRAP)

    def hit_watchpoint(self, index: int, lwpid: int | None = None, rip: int = 0,
                       regs: dict | None = None) -> None:
        """Hardware watchpoint ``index`` fires (#DB): SIGTRAP, DR6 bit ``index`` set."""
        with self._lock:
            ctx, p = self._attached_process()
            th = self._event_thread(p, lwpid)
            d7 = th.dbregs[7]
            if not dr7_enabled(d7, index):
                raise RuntimeError(f"watchpoint {index} is not enabled in thread {th.lwpid}'s DR7")
            if regs:
                th.regs.update(_check_regs(regs))
            if rip:
                th.regs["rip"] = rip
            elif dr7_access(d7, index) == 0:                 # execute breakpoints are faults
                th.regs["rip"] = th.dbregs[index]
            if not regs or "trapno" not in regs:
                th.regs["trapno"] = T_TRCTRAP
            th.dbregs[6] = DR6_RESERVED_ONES | (1 << index)
            p._trace_stop(th, SIGTRAP)

    def raise_signal(self, signal: int, lwpid: int | None = None, rip: int = 0,
                     regs: dict | None = None) -> None:
        """A signal is posted to the traced process (e.g. SIGSEGV=11 from a fault).
        SIGSTOP stops silently; SIGKILL behaves like exit_process()."""
        if signal == SIGKILL:
            return self.exit_process()
        with self._lock:
            ctx, p = self._attached_process()
            th = self._event_thread(p, lwpid)
            if regs:
                th.regs.update(_check_regs(regs))
            if rip:
                th.regs["rip"] = rip
            if (not regs or "trapno" not in regs) and signal in _SIG_TRAPNO:
                th.regs["trapno"] = _SIG_TRAPNO[signal]
            p._trace_stop(th, signal)

    def exit_process(self) -> None:
        """The attached process gets SIGKILL from outside (e.g. the shell closes the game).
        [FreeBSD] a traced process stops with WSTOPSIG==SIGKILL, so check_debug_interrupt takes
        its SIGKILL branch (server.c:82-89): debug_cleanup + debug socket closed. With
        faithful_sigkill=True the following NULL deref (server.c:86) crashes the payload, and a
        stopped process only takes the signal once it is continued."""
        with self._lock:
            ctx, p = self._attached_process()
            if p.state == "stopped":
                if self.faithful_sigkill:
                    p.pending_sigkill = True     # tdsendsignal: traced+stopped -> "no further action"
                    return
                p.wait_pending = None
            p._trace_stop(p._first_thread(), SIGKILL)

    def wait_interrupts(self, count: int, timeout: float = 3.0) -> bool:
        return self.wait_for(lambda: len(self.interrupts) >= count, timeout)

    # ------------------------------------------------------------ net helpers
    def _spawn(self, fn, name, *args):
        t = threading.Thread(target=fn, args=args, name=name, daemon=True)
        self._threads.append(t)
        t.start()
        return t

    def _shutdown_sockets(self):
        self._dead = True
        socks = [self._listener, self._udp, *self._orphans]
        with self._lock:
            for s in self._slots:
                socks += [s.sock, s.dbgctx.dbgfd]
        for s in socks:
            if s is None:
                continue
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass

    def _crash(self, reason: str):
        """The payload thread faults -> SceShellCore (which hosts it) dies -> every socket closes."""
        with self._lock:
            if self.payload_crashed:
                return
            self.payload_crashed = True
            self.crash_reason = reason
        self._shutdown_sockets()

    def _reboot(self, panic: bool = False):
        with self._lock:
            self.console_rebooted = True
            self.kernel_panicked = self.kernel_panicked or panic
        self._shutdown_sockets()

    @staticmethod
    def _send(sock: socket.socket, data: bytes):
        try:
            sock.sendall(data)
        except OSError as e:
            raise _ConnLost() from e

    def _status(self, sock, code: int):
        self._send(sock, struct.pack("<I", code))      # net.c:69-73

    @staticmethod
    def _recv_forced(sock: socket.socket, n: int) -> bytes:
        """net_recv_data(..., force=1) (net.c:38-67). On EOF the real loop spins forever."""
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = sock.recv(min(n - len(buf), 1 << 16))
            except OSError as e:
                raise _ConnLost() from e
            if not chunk:
                raise _Hang("peer closed during a forced receive")
            buf += chunk
        return bytes(buf)

    @staticmethod
    def _recv_header(sock: socket.socket) -> bytes:
        """net_recv_data(fd, &packet, 12, 0) on a SO_NBIO socket: reads what is there, maybe less."""
        buf = b""
        try:
            sock.setblocking(False)
        except OSError:
            return b""
        try:
            while len(buf) < CMD_PACKET_SIZE:
                chunk = sock.recv(CMD_PACKET_SIZE - len(buf))
                if not chunk:
                    break
                buf += chunk
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            buf = b""
        finally:
            try:
                sock.setblocking(True)
            except OSError:
                pass
        return buf

    # ------------------------------------------------------------ main loops
    def _accept_loop(self):
        ls = self._listener
        ls.settimeout(0.05 if self.accept_delay <= 0 else 0.0)
        while not self._stop_evt.is_set() and not self._dead:
            try:
                fd, addr = ls.accept()
            except (BlockingIOError, socket.timeout, TimeoutError):
                fd = None
            except OSError:
                return
            if fd is not None:
                fd.settimeout(None)
                fd.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # server.c:297-308
                with self._lock:
                    slot = next((s for s in self._slots if s.id == 0), None)   # alloc_client
                    if slot is None:
                        # server.c:430-433: no slot -> fd leaked, never served, and the sleep is skipped
                        self._orphans.append(fd)
                        continue
                    slot.reset()
                    slot.id = slot.index + 1
                    slot.sock, slot.addr = fd, addr
                self._spawn(self._handle_client, f"fake-ps4debug-client{slot.index}", slot, fd)
            if self.accept_delay > 0:
                self._stop_evt.wait(self.accept_delay)          # server.c:446 sceKernelSleep(2)

    def _broadcast_loop(self):
        us = self._udp
        while not self._stop_evt.is_set() and not self._dead:
            try:
                data, addr = us.recvfrom(4)                     # server.c:355 (4 bytes only)
            except OSError:
                return
            if len(data) == 4 and struct.unpack("<I", data)[0] == BROADCAST_MAGIC:
                try:
                    us.sendto(data, addr)                       # server.c:359-361
                except OSError:
                    pass
            # real server sleeps 1s here (server.c:366); not emulated

    def _handle_client(self, slot: _Slot, sock: socket.socket):
        """handle_client() (server.c:175-295)."""
        try:
            while not self._dead:
                try:
                    r, _, _ = select.select([sock], [], [], self.poll_interval)
                except (OSError, ValueError):
                    return
                if not r:
                    if slot.debugging:
                        if self._check_debug_interrupt(slot):
                            break
                    continue
                hdr = self._recv_header(sock)
                if not hdr:
                    break                                       # server.c:207-209
                magic, cmd, datalen = struct.unpack("<III", hdr.ljust(12, b"\0"))
                if magic != PACKET_MAGIC or len(hdr) != CMD_PACKET_SIZE:
                    continue                                    # server.c:236-245
                data = None
                if datalen:
                    if datalen > MALLOC_LIMIT:
                        break                                   # pfmalloc failed -> server.c:251-253
                    data = self._recv_forced(sock, datalen)
                with self._lock:
                    if not self.g_debugging and cmd == CMD_DEBUG_ATTACH:   # server.c:271-274
                        self._curdbgcli = slot
                        self._curdbgctx = slot.dbgctx
                self._log(cmd, data)
                if self._cmd_handler(slot, sock, cmd, data):
                    break
        except _Crash as e:
            self._crash(str(e) or "crash")
            return
        except _Hang:
            return            # thread spins forever in the real payload: slot never freed
        except _ConnLost:
            pass
        if not self._dead:
            try:
                self._free_client(slot)
            except _Crash as e:
                self._crash(str(e))

    def _log(self, cmd: int, data: bytes | None):
        name = CMD_NAMES.get(cmd, f"0x{cmd:08X}")
        fields: dict = {}
        if data is not None and cmd in _FIELDS:
            fmt, names = _FIELDS[cmd]
            fields = dict(zip(names, _unpack(fmt, data)))
        self.commands.append((name, fields))

    def _free_client(self, slot: _Slot):
        """free_client() (server.c:20-29)."""
        with self._lock:
            slot.id = 0
            sock = slot.sock
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            if slot.debugging:
                self._debug_cleanup(slot.dbgctx)
            slot.reset()

    # ------------------------------------------------------------ dispatcher
    def _cmd_handler(self, slot, sock, cmd, data) -> int:
        """cmd_handler() (server.c:38-60)."""
        if (cmd >> 24) != 0xBD:
            return 1
        if cmd == CMD_VERSION:                                  # server.c:31-36: no status
            v = self.version.encode()
            self._send(sock, struct.pack("<I", len(v)) + v)
            return 0
        group = (cmd >> 16) & 0xFF
        if group == 0xAA:
            h = self._PROC.get(cmd)
        elif group == 0xBB:
            h = self._DEBUG.get(cmd)
        elif group == 0xCC:
            h = self._KERN.get(cmd)
        elif group == 0xDD:
            h = self._CONSOLE.get(cmd)
            if h is None:
                return 0                                        # console.c:196 unknown -> 0
        else:
            return 0
        if h is None:
            return 1                                            # proc.c:818, debug.c:641, kern.c:86
        return h(self, slot, sock, data)

    # ------------------------------------------------------------ memory ops
    def _live(self, pid: int) -> FakeProcess | None:
        """proc_find_by_pid (kdebugger proc.c:24-35): walks allproc, zombies are not there."""
        p = self.processes.get(pid)
        return p if p is not None and p.alive else None

    def _rw(self, pid: int, address: int, length: int, data: bytes | None = None,
            out: bytearray | None = None) -> bool:
        """sys_proc_rw -> proc_rwmem (kdebugger proc.c:89-127) [FreeBSD]: page at a time, the
        first page that is not CPU-readable (or not writable for writes) aborts the transfer;
        bytes already copied stay copied."""
        p = self._live(pid)
        if p is None:
            return False
        done = 0
        while done < length:
            uva = (address + done) & MASK64
            pg = page_trunc(uva)
            n = min(PAGE_SIZE - (uva - pg), length - done)
            m = p._mapping_at(pg)
            if m is None or not (m.prot & 1) or (data is not None and not (m.max_prot & 2)):
                return False
            off = uva - m.start
            if m.end - uva < n:
                n = m.end - uva
            if data is None:
                out[done:done + n] = m.data[off:off + n]
            else:
                m.data[off:off + n] = data[done:done + n]
            done += n
        return True

    def _read_mem(self, pid, address, length) -> bytes:
        buf = bytearray(length)
        self._rw(pid, address, length, out=buf)
        return bytes(buf)

    def _proc_allocate(self, p: FakeProcess, size: int, name: str = "") -> int | None:
        """proc_allocate (kdebugger proc.c:137-172): vm_map_findspace = first fit from min_offset."""
        aligned = (size + 0x3FFF) & ~0x3FFF
        if aligned == 0:
            return None                                       # vm_map_insert(start == end) fails
        addr = VM_MIN_USER
        for m in p.mappings:
            if addr + size <= m.start:
                break
            addr = max(addr, m.end)
        if addr + aligned > VM_MAX_USER:
            return None
        p._insert(_Mapping(name, addr, addr + aligned, 7, 0))
        return addr

    def _proc_vm_map(self, p: FakeProcess) -> list[tuple]:
        """sys_proc_vm_map_handle (kdebugger hooks.c:93-127)."""
        real = [(m.name, m.start, m.end, m.offset, m.prot & m.max_prot) for m in p.mappings]
        n = len(real)
        if not self.maps_header_quirk or n == 0:
            return real
        # vm_map_lookup_entry(map, NULL, &entry) -> &map->header (start=min_offset,end=max_offset)
        return [("", VM_MIN_USER, VM_MAX_USER, 0, 0)] + real[:n - 1]

    def _create_thread(self, p: FakeProcess, name: str) -> int | None:
        """proc_create_thread (kdebugger proc.c:211-356): needs an r-x libkernel*.sprx mapping to
        find scePthreadCreate; without it the function returns 0 WITHOUT creating a thread."""
        if not any(m.prot & m.max_prot == 5 and m.name.startswith(("libkernel.sprx", "libkernel_web.sprx",
                                                                     "libkernel_sys.sprx"))
                   for m in p.mappings):
            return None
        lwpid = max([t for q in self.processes.values() for t in q.threads] + [100000]) + 1
        p.add_thread(lwpid, name, 700)
        return lwpid

    # ------------------------------------------------------------- proc cmds
    def _proc_list(self, slot, sock, data):                   # proc.c:7-35
        with self._lock:
            procs = [p for p in reversed(list(self.processes.values())) if p.alive]  # allproc: newest first
            if not procs:
                self._status(sock, CMD_DATA_NULL)
                return 1
            body = b"".join(PROC_LIST_ENTRY.pack(p.name.encode()[:32], p.pid) for p in procs)
        self._status(sock, CMD_SUCCESS)
        self._send(sock, struct.pack("<I", len(procs)) + body)
        return 0

    def _proc_read(self, slot, sock, data):                   # proc.c:37-85
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 1
        pid, address, left = _unpack("<IQI", data)
        self._status(sock, CMD_SUCCESS)
        while left > 0:
            n = min(left, NET_MAX_LENGTH)
            with self._lock:
                chunk = self._read_mem(pid, address, n)       # memset(0) + sys_proc_rw
            self._send(sock, chunk)
            address = (address + n) & MASK64
            left -= n
        return 0

    def _proc_write(self, slot, sock, data):                  # proc.c:87-135
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 1
        pid, address, left = _unpack("<IQI", data)
        self._status(sock, CMD_SUCCESS)
        while left > 0:
            n = min(left, NET_MAX_LENGTH)
            chunk = self._recv_forced(sock, n)
            with self._lock:
                self._rw(pid, address, n, data=chunk)          # failures are ignored
            address = (address + n) & MASK64
            left -= n
        self._status(sock, CMD_SUCCESS)
        return 0

    def _proc_maps(self, slot, sock, data):                   # proc.c:137-180
        if not data:
            self._status(sock, CMD_ERROR)
            return 1
        pid, = _unpack("<I", data)
        with self._lock:
            p = self._live(pid)
            if p is None:
                self._status(sock, CMD_ERROR)
                return 1
            entries = self._proc_vm_map(p)
        body = b"".join(PROC_VM_MAP_ENTRY.pack(n.encode()[:32], s, e, o, pr & 0xFFFF) for n, s, e, o, pr in entries)
        self._status(sock, CMD_SUCCESS)
        self._send(sock, struct.pack("<I", len(entries)) + body)
        return 0

    def _proc_install(self, slot, sock, data):                # proc.c:182-208, hooks.c:129-153
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 1
        pid, = _unpack("<I", data)
        with self._lock:
            p = self._live(pid)
            stub = self._proc_allocate(p, len(RPCSTUB) + (PAGE_SIZE - len(RPCSTUB) % PAGE_SIZE)) if p else None
            if stub is not None:
                self._rw(pid, stub, len(RPCSTUB), data=RPCSTUB)
                p.rpc_stubs[stub] = self._create_thread(p, "rpcstub")
                if p.rpc_stubs[stub] is not None:   # a thread do stub resolve sceKernelUsleep e grava em +0x9B
                    self._rw(pid, stub + 0x9B, 8, data=struct.pack("<Q", 0x800001230))
        if not stub:
            self._status(sock, CMD_DATA_NULL)
            return 1
        self._status(sock, CMD_SUCCESS)
        self._send(sock, struct.pack("<Q", stub))
        return 0

    def _proc_call(self, slot, sock, data):                   # proc.c:210-243, hooks.c:155-194
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 1
        pid, stub, rip, *args = _unpack("<IQQQQQQQQ", data)
        rax = 0
        with self._lock:
            p = self._live(pid)
            ok = p is not None and self._rw(pid, stub + RPCSTUB_RIP_OFF, 56, data=struct.pack("<7Q", rip, *args)) \
                and self._rw(pid, stub + RPCSTUB_GO_OFF, 1, data=b"\x01")
            if ok:
                lwp = p.rpc_stubs.get(stub)
                th = p.threads.get(lwp) if lwp is not None else None
                if th is None or th.suspended or p.state != "running":
                    raise _Hang("kernel busy-waits on rpc_done forever (stub thread not running)")
                fn = p.call_handlers.get(rip) or next(
                    (f for code, f in p.code_handlers if p.read(rip, len(code)) == code), None)
                rax = (fn(*args) if fn else 0) & MASK64
                self._rw(pid, stub + RPCSTUB_RAX_OFF, 8, data=struct.pack("<Q", rax))
                self._rw(pid, stub + RPCSTUB_GO_OFF, 2, data=b"\x00\x00")
        self._status(sock, CMD_SUCCESS)
        self._send(sock, struct.pack("<IQ", pid, rax))
        return 0

    def _proc_elf(self, slot, sock, data):                    # proc.c:245-281, hooks.c:196-228
        if not data:
            self._status(sock, CMD_ERROR)
            return 1
        pid, length = _unpack("<II", data)
        if length > MALLOC_LIMIT:
            self._status(sock, CMD_DATA_NULL)
            return 1
        self._status(sock, CMD_SUCCESS)
        elf = self._recv_forced(sock, length)
        with self._lock:
            p = self._live(pid)
            ok = p is not None and self._load_elf(p, elf)
        if not ok:
            self._status(sock, CMD_ERROR)
            return 1
        self._status(sock, CMD_SUCCESS)
        return 0

    def _load_elf(self, p: FakeProcess, elf: bytes) -> bool:
        """proc_load_elf (kdebugger proc.c:358-465, elf.c): uses p_paddr, only R_X86_64_RELATIVE."""
        if elf[:4] != b"\x7fELF" or len(elf) < 64:
            return False
        e_entry, e_phoff, e_shoff = struct.unpack_from("<QQQ", elf, 0x18)
        e_phentsize, e_phnum, e_shentsize, e_shnum = struct.unpack_from("<HHHH", elf, 0x36)
        try:
            segs = []
            if e_phoff:
                for i in range(e_phnum):
                    ph = struct.unpack_from("<IIQQQQQQ", elf, e_phoff + i * e_phentsize)
                    segs.append((ph[4], ph[6], ph[2], ph[5]))          # paddr, memsz, offset, filesz
            else:
                for i in range(e_shnum):
                    sh = struct.unpack_from("<IIQQQQIIQQ", elf, e_shoff + i * e_shentsize)
                    if sh[2] & 2:                                       # SHF_ALLOC
                        segs.append((sh[3], sh[5], sh[4], sh[5]))
        except struct.error:
            return False
        msize = max([a + m for a, m, _o, _f in segs] + [0])
        msize += PAGE_SIZE - msize % PAGE_SIZE
        base = self._proc_allocate(p, msize)
        if base is None:
            return False
        for a, _m, off, filesz in segs:
            if filesz:
                self._rw(p.pid, base + a, filesz, data=elf[off:off + filesz])
        for m in p.mappings:                                   # hooks.c:211-220
            if m.prot & m.max_prot == 5 and m.name.startswith("executable"):
                m.prot = m.max_prot = 7
                break
        self._create_thread(p, "elf")
        p.loaded_elfs.append((base, elf))
        return True

    def _proc_protect(self, slot, sock, data):                # proc.c:283-301 (two statuses!)
        if data:
            pid, address, length, newprot = _unpack("<IQII", data)
            with self._lock:
                p = self._live(pid)
                if p is not None:                               # kdebugger proc.c:190-209
                    end = address + ((length + 0x3FFF) & ~0x3FFF)
                    for m in p._clip(page_trunc(address), page_round(end)):
                        m.max_prot = newprot & 0xFF
                        m.prot = newprot & 0xFF
            self._status(sock, CMD_SUCCESS)
        self._status(sock, CMD_DATA_NULL)                     # falls through: always sent
        return 0

    def _proc_info(self, slot, sock, data):                   # proc.c:721-745, hooks.c:230-237
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 0
        pid, = _unpack("<I", data)
        with self._lock:
            p = self._live(pid)
            if p is None:   # real: uninitialised stack garbage
                resp = bytes(PROC_INFO_RESPONSE.size)
            else:
                resp = PROC_INFO_RESPONSE.pack(p.pid, p.name.encode()[:32], p.path.encode()[:64],
                                               p.titleid.encode()[:16], p.contentid.encode()[:64])
        self._status(sock, CMD_SUCCESS)
        self._send(sock, resp)
        return 0

    def _proc_alloc(self, slot, sock, data):                  # proc.c:747-768
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 0
        pid, length = _unpack("<II", data)
        with self._lock:
            p = self._live(pid)
            addr = self._proc_allocate(p, length) if p else None
        self._status(sock, CMD_SUCCESS)
        self._send(sock, struct.pack("<Q", addr or 0))         # real: garbage on failure
        return 0

    def _proc_free(self, slot, sock, data):                   # proc.c:770-788
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 0
        pid, address, length = _unpack("<IQI", data)
        with self._lock:
            p = self._live(pid)
            if p is not None:                                   # kdebugger proc.c:174-188
                end = address + ((length + 0x3FFF) & ~0x3FFF)
                for m in p._clip(page_trunc(address), page_round(end)):
                    p.mappings.remove(m)
        self._status(sock, CMD_SUCCESS)
        return 0

    def _proc_scan(self, slot, sock, data):
        """proc_scan_handle (proc.c:633-719), bugs included:

        * the page buffer is always refilled from the FIRST page of the section
          (proc.c:698 reads ``sectionStartAddr``, not start+j), so every page
          of a section is compared against page 0's bytes;
        * candidates are only offsets that are multiples of the value length
          (proc.c:696, j += valueLength), including for strings/byte arrays;
        * exact compare skips the last byte (proc.c:332 ``j < valTypeLength-1``):
          1-byte exact scans never match, uint32 matches on 3 bytes;
        * values past the page end read beyond the 16 KiB buffer (zeros here);
        * the maps come from the same vm_map walk as PROC_MAPS (header entry,
          last entry missing);
        * compare types needing the extra value dereference NULL when lenData ==
          value length -> payload crash; lenData 0 for byte arrays -> infinite loop.
        """
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 1
        pid, vtype, ctype, len_data = _unpack("<IBBI", data)
        vlen = _SCAN_SIZES.get(vtype, 0) or len_data
        if len_data > MALLOC_LIMIT:
            self._status(sock, CMD_DATA_NULL)
            return 1
        self._status(sock, CMD_SUCCESS)
        values = self._recv_forced(sock, len_data)
        with self._lock:
            p = self._live(pid)
            if p is None:
                self._status(sock, CMD_ERROR)
                return 1
            maps = self._proc_vm_map(p)
        self._status(sock, CMD_SUCCESS)
        extra = None if vlen == len_data else values[vlen:vlen + vlen].ljust(vlen, b"\0")
        scan = values[:vlen].ljust(vlen, b"\0")
        results: list[int] = []
        buf = bytearray(PAGE_SIZE)
        with self._lock:
            for _n, start, end, _o, prot in maps:
                if not (prot & 1):
                    continue
                if vlen == 0:
                    raise _Hang("PROC_SCAN with value length 0 loops forever (j += 0)")
                section_len = end - start
                tmp = bytearray(PAGE_SIZE)
                if self._rw(pid, start, PAGE_SIZE, out=tmp):
                    buf = tmp                      # on failure the old buffer contents stay
                padded = bytes(buf) + bytes(vlen)
                if PAGE_SIZE % vlen == 0:
                    offs = [o for o in range(0, PAGE_SIZE, vlen)
                            if _scan_compare(ctype, vtype, vlen, scan, padded[o:o + vlen], extra)]
                    for k in range(0, section_len, PAGE_SIZE):
                        results += [start + k + o for o in offs if k + o < section_len]
                else:
                    cache: dict[int, bool] = {}
                    for j in range(0, section_len, vlen):
                        o = j % PAGE_SIZE
                        hit = cache.get(o)
                        if hit is None:
                            hit = cache[o] = _scan_compare(ctype, vtype, vlen, scan, padded[o:o + vlen], extra)
                        if hit:
                            results.append(start + j)
        self._send(sock, b"".join(struct.pack("<Q", a) for a in results) + struct.pack("<Q", MASK64))
        return 0

    # ------------------------------------------------------------ debug cmds
    def _ctx(self) -> _DebugContext:
        ctx = self._curdbgctx
        if ctx is None:
            raise _Crash("NULL curdbgctx dereference (debug command without an attached debugger)")
        return ctx

    def _thread_for(self, ident: int) -> tuple[FakeProcess, FakeThread] | None:
        """ptrace target resolution [FreeBSD]: ids > PID_MAX are lwpids (tdfind); otherwise a
        pid -> p_xthread when trace-stopped, else the first thread."""
        for p in self.processes.values():
            if p.alive and ident in p.threads:
                return p, p.threads[ident]
        p = self._live(ident)
        if p is not None and p.threads:
            th = p.threads.get(p.xthread) if p.state == "stopped" and p.xthread else None
            return p, th or p._first_thread()
        return None

    def _set_dbregs(self, th: FakeThread, dr: list[int]) -> bool:
        """[FreeBSD amd64] set_dbregs() validation."""
        d7 = dr[7]
        if (dr[6] >> 32) or (d7 >> 32):
            return False
        for i in range(4):
            if dr7_access(d7, i) == 2:
                return False
            if dr7_enabled(d7, i) and dr[i] >= VM_MAX_USER:
                return False
        th.dbregs = [dr[0], dr[1], dr[2], dr[3], 0, 0, dr[6], dr[7]] + [0] * 8
        return True

    def _debug_attach(self, slot, sock, data):               # debug.c:11-56
        with self._lock:
            if self.g_debugging:
                self._status(sock, CMD_ALREADY_DEBUG)
                return 1
            if not data:
                self._status(sock, CMD_DATA_NULL)
                return 1
            pid, = _unpack("<I", data)
            p = self._live(pid)
            # PT_ATTACH fails: no such pid, P_SYSTEM (pid 0), self (the payload runs inside
            # SceShellCore, installer.c load_debugger), already traced (EBUSY)
            if p is None or pid == 0 or p.name == "SceShellCore" or p.traced:
                self._status(sock, CMD_ERROR)
                return 1
            p.traced = True                        # PT_ATTACH + PT_CONTINUE (debug.c:23-33)
            if p.state == "stopped":
                p._resume()
            self.g_debugging = True                # connect_debugger (debug.c:544) - before connecting
            ctx = self._curdbgctx
            host = slot.addr[0]
        try:
            dbg = socket.create_connection((host, self.debug_port), timeout=self.connect_timeout)
            dbg.settimeout(None)
            dbg.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            # g_debugging stays 1 and the process stays traced: later ATTACHes -> CMD_ALREADY_DEBUG
            self._status(sock, CMD_ERROR)
            return 1
        with self._lock:
            ctx.dbgfd = dbg
            ctx.peer_fin_sends = 0
            slot.debugging = True                  # debug.c:43-44
            ctx.pid = pid
            self._last_debug_pid = pid
        self._status(sock, CMD_SUCCESS)            # only after the debug connection exists
        return 0

    def _debug_detach(self, slot, sock, data):               # debug.c:58-64
        with self._lock:
            self._debug_cleanup(self._ctx())
        self._status(sock, CMD_SUCCESS)
        return 0

    def _debug_cleanup(self, ctx: _DebugContext):            # debug.c:566-605
        if self._curdbgcli is None:
            raise _Crash("NULL curdbgcli dereference in debug_cleanup")
        self._curdbgcli.debugging = False
        self.g_debugging = False
        self._curdbgcli = None
        self._curdbgctx = None
        for _en, addr, orig in ctx.breakpoints:              # writes ALL 30 slots back
            self._rw(ctx.pid, addr, 1, data=bytes([orig]))
        p = self._live(ctx.pid) if ctx.pid else None
        if p is not None:
            for th in p.threads.values():
                th.dbregs = [0] * 16
            if p.state == "stopped":
                p._resume()                                  # PT_CONTINUE
            p.traced = False                                  # PT_DETACH
            p.pending_sigkill = False
        if ctx.dbgfd is not None:
            try:
                ctx.dbgfd.close()
            except OSError:
                pass

    def _debug_breakpt(self, slot, sock, data):              # debug.c:66-107
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            if not data:
                self._status(sock, CMD_DATA_NULL)
                return 1
            index, enabled, address = _unpack("<IIQ", data)
            if index >= MAX_BREAKPOINTS:
                self._status(sock, CMD_INVALID_INDEX)
                return 1
            bp = ctx.breakpoints[index]
            if enabled:
                bp[0], bp[1] = 1, address
                b = bytearray(1)
                if self._rw(ctx.pid, address, 1, out=b):     # unreadable -> original unchanged
                    bp[2] = b[0]
                self._rw(ctx.pid, address, 1, data=b"\xcc")
            else:
                self._rw(ctx.pid, bp[1], 1, data=bytes([bp[2]]))
                bp[0], bp[1] = 0, 0
        self._status(sock, CMD_SUCCESS)
        return 0

    def _debug_watchpt(self, slot, sock, data):              # debug.c:109-178
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            if not data:
                self._status(sock, CMD_DATA_NULL)
                return 1
            index, enabled, length, breaktype, address = _unpack("<IIIIQ", data)
            if index >= MAX_WATCHPOINTS:
                self._status(sock, CMD_INVALID_INDEX)
                return 1
            p = self._live(ctx.pid)
            if p is None:                                     # PT_GETNUMLWPS -1 -> pfmalloc fails
                self._status(sock, CMD_DATA_NULL)
                return 1
            dr = ctx.watchdata
            dr[7] &= ~dr7_mask(index) & MASK64
            if enabled:
                dr[index] = address
                dr[7] |= dr7_set(index, length, breaktype, 3)
            else:
                dr[index] = 0
                dr[7] |= dr7_set(index, 0, 0, 0)
            for th in p.threads.values():
                if not self._set_dbregs(th, dr):
                    self._status(sock, CMD_ERROR)
                    return 1                                  # returns r == -1 -> disconnect
        self._status(sock, CMD_SUCCESS)
        return 0

    def _debug_threads(self, slot, sock, data):              # debug.c:180-220
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            p = self._live(ctx.pid)
            if p is None:
                self._status(sock, CMD_ERROR)
                return 0
            lwps = list(p.threads)
        self._status(sock, CMD_SUCCESS)
        self._send(sock, struct.pack("<i", len(lwps)) + struct.pack(f"<{len(lwps)}I", *lwps))
        return 0

    def _debug_stopresume_thr(self, slot, sock, data, suspend: bool):   # debug.c:222-274
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            if not data:
                self._status(sock, CMD_DATA_NULL)
                return 1
            lwpid, = _unpack("<I", data)
            t = self._thread_for(lwpid)
            if t is None:
                self._status(sock, CMD_ERROR)
                return 0
            t[1].suspended = suspend
        self._status(sock, CMD_SUCCESS)
        return 0

    def _debug_getx(self, slot, sock, data, kind: str):     # debug.c:276-361
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            if not data:
                self._status(sock, CMD_DATA_NULL)
                return 1
            lwpid, = _unpack("<I", data)
            t = self._thread_for(lwpid)
            if t is None:
                self._status(sock, CMD_ERROR)
                return 1
            th = t[1]
            body = {"regs": th.pack_regs, "fp": lambda: bytes(th.fpregs),
                    "db": lambda: DBREG64.pack(*th.dbregs)}[kind]()
        self._status(sock, CMD_SUCCESS)
        self._send(sock, body)
        return 0

    def _debug_setx(self, slot, sock, data, kind: str):     # debug.c:363-452
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            if not data:
                self._status(sock, CMD_ERROR)
                return 1
            _lwpid, length = _unpack("<II", data)   # lwpid is IGNORED: ptrace(..., curdbgctx->pid)
        self._status(sock, CMD_SUCCESS)
        blob = self._recv_forced(sock, length)
        if kind == "fp":
            # debug.c:410 receives into &fpregs (an 8-byte pointer on the stack) and passes the
            # received bytes as a pointer -> EFAULT; >8 bytes smash the stack -> crash on return
            self._status(sock, CMD_ERROR)
            if length > 8:
                raise _Crash("SETFPREGS stack overflow (net_recv_data into &fpregs)")
            return 1
        limit = REG64.size if kind == "regs" else DBREG64.size
        if length > limit:
            raise _Crash(f"SET{kind.upper()}REGS stack buffer overflow ({length} > {limit})")
        with self._lock:
            t = self._thread_for(ctx.pid)
            ok = t is not None
            if ok:
                blob = blob.ljust(limit, b"\0")   # real: uninitialised stack for the tail
                if kind == "regs":
                    t[1].regs = dict(zip(REG_NAMES, REG64.unpack(blob)))
                else:
                    ok = self._set_dbregs(t[1], list(DBREG64.unpack(blob)))
        if not ok:
            self._status(sock, CMD_ERROR)
            return 1
        self._status(sock, CMD_SUCCESS)
        return 0

    def _debug_stopgo(self, slot, sock, data):               # debug.c:454-488
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            if not data:
                self._status(sock, CMD_DATA_NULL)
                return 1
            stop, = _unpack("<I", data)
            p = self._live(ctx.pid)
            if p is None:                                     # ESRCH
                self._status(sock, CMD_ERROR)
                return 1
            if stop == 1:     # PT_CONTINUE(SIGSTOP): running -> SIGSTOP trace stop (no interrupt)
                if p.state == "running":
                    p._trace_stop(p._first_thread(), SIGSTOP)
            elif stop == 2:   # PT_CONTINUE(SIGKILL)
                if p.state == "stopped" and self.faithful_sigkill:
                    # resumes with SIGKILL delivered -> exits; wait4 reports WIFSIGNALED (status 9)
                    p.state = "killed"
                    p.traced = False
                    p.xthread = None
                    p.wait_pending = SIGKILL
                else:         # traced running process stops with SIGKILL -> server.c:82 branch
                    p.wait_pending = None
                    p._trace_stop(p._first_thread(), SIGKILL)
            else:             # PT_CONTINUE(0)
                if p.state == "stopped":
                    p._resume()
        self._status(sock, CMD_SUCCESS)
        return 0

    def _debug_thrinfo(self, slot, sock, data):              # debug.c:490-518, hooks.c:239-255
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            if not data:
                self._status(sock, CMD_DATA_NULL)
                return 1
            lwpid, = _unpack("<I", data)
            p = self._live(ctx.pid)
            th = p.threads.get(lwpid) if p else None
            # not found: priority/name are uninitialised stack in the real payload (zeros here)
            resp = THRINFO_RESPONSE.pack(lwpid, th.priority if th else 0,
                                         th.name.encode()[:32] if th else b"")
        self._status(sock, CMD_SUCCESS)
        self._send(sock, resp)
        return 0

    def _debug_singlestep(self, slot, sock, data):           # debug.c:520-537
        with self._lock:
            ctx = self._ctx()
            if ctx.pid == 0:
                self._status(sock, CMD_ERROR)
                return 1
            p = self._live(ctx.pid)
            # PT_STEP needs a stopped process (stock FreeBSD EBUSY) -> CMD_ERROR + close
            if p is None or p.state != "stopped" or not p.threads:
                self._status(sock, CMD_ERROR)
                return 1
            th = p.threads.get(p.xthread) if p.xthread else None
            th = th or p._first_thread()
            p._resume()
            if p.state == "running" and not th.suspended:
                rip = th.regs["rip"]
                code = self._read_mem(p.pid, rip, 16)
                th.regs["rip"] = (rip + insn_length(code, rip)) & MASK64
                th.regs["trapno"] = T_BPTFLT if code[:1] == b"\xcc" else T_TRCTRAP
                p._trace_stop(th, SIGTRAP)
        self._status(sock, CMD_SUCCESS)
        return 0

    # --------------------------------------------- check_debug_interrupt()
    def _check_debug_interrupt(self, slot: _Slot) -> bool:
        """server.c:62-173. Returns True when the client must be freed (ECONNRESET on dbgfd)."""
        with self._lock:
            ctx = self._curdbgctx
            if ctx is None or not slot.debugging:
                return False
            p = self.processes.get(ctx.pid)
            r, status = p._wait4() if p else (-1, None)
            if r == 0 or r == -1:      # nothing / ECHILD (real code then reads garbage: not emulated)
                return False
            sig = (status >> 8) & 0xFF
            stopped = (status & 0xFF) == 0x7F
            if stopped and sig == SIGSTOP:
                return False                                   # server.c:79-81
            if stopped and sig == SIGKILL:                     # server.c:82-89
                self._debug_cleanup(ctx)
                if self.faithful_sigkill:
                    # cleanup's PT_CONTINUE(0) swallowed the SIGKILL; then curdbgctx->pid derefs NULL
                    raise _Crash("NULL curdbgctx dereference after debug_cleanup (server.c:86)")
                p.state = "killed"                             # intended: PT_CONTINUE(SIGKILL)
                p.traced = False
                p.reaped = True
                return False
            th = p.threads.get(p.xthread) if (p.alive and p.xthread) else None
            if th is not None:
                regs = dict(th.regs)
                hdr = INTERRUPT_HEADER.pack(th.lwpid, status, th.name.encode()[:24])  # pl_tdname[24]
                fp, db = bytes(th.fpregs), DBREG64.pack(*th.dbregs)
            else:                       # dead process: PT_LWPINFO/GETREGS fail, zeroed packet
                regs = {k: 0 for k in REG_NAMES}
                hdr = INTERRUPT_HEADER.pack(0, status, b"")
                fp, db = bytes(SAVEFPU_YMM_SIZE), bytes(DBREG64.size)
            target = (regs["rip"] - 1) & MASK64
            bp = next((b for b in ctx.breakpoints if b[1] == target), None)
            if bp is not None and th is not None:              # software breakpoint (server.c:137-158)
                self._rw(ctx.pid, bp[1], 1, data=bytes([bp[2]]))
                regs["rip"] = target
                code = self._read_mem(ctx.pid, target, 16)
                th.regs["rip"] = (target + insn_length(code, target)) & MASK64  # PT_STEP over it
                self._rw(ctx.pid, bp[1], 1, data=b"\xcc")
            pkt = hdr + th.pack_regs(regs) + fp + db if th is not None else hdr + REG64.pack(
                *[0] * 26) + fp + db
            assert len(pkt) == DEBUG_INTERRUPT_PACKET_SIZE
            dbg = ctx.dbgfd
        reset = self._send_interrupt(ctx, dbg, pkt)
        with self._lock:
            self.interrupts.append({"lwpid": struct.unpack_from("<I", pkt, 0)[0], "status": status,
                                    "rip": regs["rip"], "delivered": not reset})
        return reset

    def _send_interrupt(self, ctx: _DebugContext, dbg: socket.socket | None, pkt: bytes) -> bool:
        """net_send_data on dbgfd. [FreeBSD] after the peer closed, the first write still
        "succeeds" (peer answers RST); the next one fails with ECONNRESET, which handle_client
        treats as a disconnect of the attached client (server.c:224-227)."""
        if dbg is None:
            return False
        try:
            r, _, _ = select.select([dbg], [], [], 0)
            peer_closed = bool(r) and dbg.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            peer_closed = True
        if peer_closed:
            ctx.peer_fin_sends += 1
            return ctx.peer_fin_sends > 1
        try:
            dbg.sendall(pkt)
        except OSError as e:
            return getattr(e, "errno", None) == _errno.ECONNRESET
        return False

    # ------------------------------------------------------------- kern cmds
    def _kern_range_ok(self, address: int, length: int) -> bool:
        return self.kernel_base <= address and address + length <= self.kernel_base + len(self.kernel_memory)

    def _kern_base(self, slot, sock, data):                  # kern.c:7-16
        self._status(sock, CMD_SUCCESS)
        self._send(sock, struct.pack("<Q", self.kernel_base))
        return 0

    def _kern_read(self, slot, sock, data):                  # kern.c:18-44
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 0
        address, length = _unpack("<QI", data)
        if length > MALLOC_LIMIT:
            self._status(sock, CMD_DATA_NULL)
            return 1
        if length and not self._kern_range_ok(address, length):
            self._reboot(panic=True)    # kernel page fault -> hook_trap_fatal -> kern_reboot(4)
            raise _Hang("kernel panic")
        off = address - self.kernel_base
        body = bytes(self.kernel_memory[off:off + length]) if length else b""
        self._status(sock, CMD_SUCCESS)
        self._send(sock, body)
        return 0

    def _kern_write(self, slot, sock, data):                 # kern.c:46-74
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 0
        address, length = _unpack("<QI", data)
        if length > MALLOC_LIMIT:
            self._status(sock, CMD_DATA_NULL)
            return 1
        self._status(sock, CMD_SUCCESS)
        blob = self._recv_forced(sock, length)
        if length and not self._kern_range_ok(address, length):
            self._reboot(panic=True)
            raise _Hang("kernel panic")
        off = address - self.kernel_base
        self.kernel_memory[off:off + length] = blob
        self._status(sock, CMD_SUCCESS)
        return 0

    # ---------------------------------------------------------- console cmds
    def _console_reboot(self, slot, sock, data):             # console.c:7-18 (no status)
        with self._lock:
            if self.g_debugging:
                self._debug_cleanup(self._curdbgctx)
        self._reboot()
        raise _Hang("console rebooting")

    def _console_end(self, slot, sock, data):                # console.c:100
        return 1

    def _console_text(self, sock, length: int) -> str:
        raw = self._recv_forced(sock, length) if length else b""
        return raw.split(b"\0", 1)[0].decode("utf-8", "replace")

    def _console_print(self, slot, sock, data):              # console.c:20-48 (no status before text)
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 1
        length, = _unpack("<I", data)
        text = self._console_text(sock, length)
        with self._lock:
            self.print_log.append(text)
            self.notifications.append(text)
        self._status(sock, CMD_SUCCESS)
        return 0

    def _console_notify(self, slot, sock, data):             # console.c:50-78
        if not data:
            self._status(sock, CMD_DATA_NULL)
            return 1
        mtype, length = _unpack("<II", data)
        text = self._console_text(sock, length)
        with self._lock:
            self.notify_log.append((mtype, text))
            self.notifications.append(text)
        self._status(sock, CMD_SUCCESS)
        return 0

    def _console_info(self, slot, sock, data):               # console.c:80-93: status only
        self._status(sock, CMD_SUCCESS)
        return 0

    _PROC = {
        CMD_PROC_LIST: _proc_list, CMD_PROC_READ: _proc_read, CMD_PROC_WRITE: _proc_write,
        CMD_PROC_MAPS: _proc_maps, CMD_PROC_INTALL: _proc_install, CMD_PROC_CALL: _proc_call,
        CMD_PROC_ELF: _proc_elf, CMD_PROC_PROTECT: _proc_protect, CMD_PROC_SCAN: _proc_scan,
        CMD_PROC_INFO: _proc_info, CMD_PROC_ALLOC: _proc_alloc, CMD_PROC_FREE: _proc_free,
    }
    _DEBUG = {
        CMD_DEBUG_ATTACH: _debug_attach, CMD_DEBUG_DETACH: _debug_detach,
        CMD_DEBUG_BREAKPT: _debug_breakpt, CMD_DEBUG_WATCHPT: _debug_watchpt,
        CMD_DEBUG_THREADS: _debug_threads,
        CMD_DEBUG_STOPTHR: lambda s, sl, so, d: s._debug_stopresume_thr(sl, so, d, True),
        CMD_DEBUG_RESUMETHR: lambda s, sl, so, d: s._debug_stopresume_thr(sl, so, d, False),
        CMD_DEBUG_GETREGS: lambda s, sl, so, d: s._debug_getx(sl, so, d, "regs"),
        CMD_DEBUG_GETFPREGS: lambda s, sl, so, d: s._debug_getx(sl, so, d, "fp"),
        CMD_DEBUG_GETDBGREGS: lambda s, sl, so, d: s._debug_getx(sl, so, d, "db"),
        CMD_DEBUG_SETREGS: lambda s, sl, so, d: s._debug_setx(sl, so, d, "regs"),
        CMD_DEBUG_SETFPREGS: lambda s, sl, so, d: s._debug_setx(sl, so, d, "fp"),
        CMD_DEBUG_SETDBGREGS: lambda s, sl, so, d: s._debug_setx(sl, so, d, "db"),
        CMD_DEBUG_STOPGO: _debug_stopgo, CMD_DEBUG_THRINFO: _debug_thrinfo,
        CMD_DEBUG_SINGLESTEP: _debug_singlestep,
    }
    _KERN = {CMD_KERN_BASE: _kern_base, CMD_KERN_READ: _kern_read, CMD_KERN_WRITE: _kern_write}
    _CONSOLE = {
        CMD_CONSOLE_REBOOT: _console_reboot, CMD_CONSOLE_END: _console_end,
        CMD_CONSOLE_PRINT: _console_print, CMD_CONSOLE_NOTIFY: _console_notify,
        CMD_CONSOLE_INFO: _console_info,
    }


# --------------------------------------------------------------------------
# PROC_SCAN comparison, mirroring proc_scan_compareValues (proc.c:326-631)
# --------------------------------------------------------------------------
_SCAN_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 8, 7: 8, 8: 4, 9: 8}      # proc.c:303-324
_SCAN_FMT = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<Q", 7: "<q", 8: "<f", 9: "<d"}


def _f32(x: float) -> float:
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return float("inf") if x > 0 else float("-inf")


def _wrap(vt: int, x):
    """C arithmetic result type: 8/16-bit operands promote to int (no wrap); 32/64 wrap."""
    if vt in (4, 6):
        return x & ((1 << (32 if vt == 4 else 64)) - 1)
    if vt in (5, 7):
        bits = 32 if vt == 5 else 64
        x &= (1 << bits) - 1
        return x - (1 << bits) if x >> (bits - 1) else x
    if vt == 8:
        return _f32(x)
    return x


def _scan_compare(ct: int, vt: int, vlen: int, scan: bytes, mem: bytes, extra: bytes | None) -> bool:
    if ct == 0:                                            # proc.c:329-338 (last byte skipped)
        found = False
        for j in range(vlen - 1):
            found = scan[j] == mem[j]
            if not found:
                break
        return found
    if ct == 1:                                            # proc.c:339-352
        if vt == 8:
            d = _f32(struct.unpack("<f", scan[:4])[0] - struct.unpack("<f", mem[:4])[0])
            return -1.0 < d < 1.0
        if vt == 9:
            d = struct.unpack("<d", scan[:8])[0] - struct.unpack("<d", mem[:8])[0]
            return -1.0 < d < 1.0
        return False
    if ct == 11:                                           # cmpTypeUnknownInitialValue
        return True
    if not 2 <= ct <= 10:
        return False
    if vt in (10, 11):
        return False
    if vt not in _SCAN_FMT:
        return True        # no inner case matches: falls through every case to UnknownInitial
    fmt = _SCAN_FMT[vt]
    m = struct.unpack(fmt, mem[:_SCAN_SIZES[vt]])[0]
    s = struct.unpack(fmt, scan[:_SCAN_SIZES[vt]])[0]
    if ct == 2:
        return m > s
    if ct == 3:
        return m < s
    if extra is None:
        raise _Crash("PROC_SCAN dereferences NULL pExtraValue")
    e = struct.unpack(fmt, extra[:_SCAN_SIZES[vt]])[0]
    if vt == 9 and ct in (6, 8):        # proc.c:507/563: double compares use *(float *)pScanValue
        s = struct.unpack("<f", scan[:4])[0]
    if ct == 4:
        return (s < m < e) if e > s else (e < m < s)
    if ct == 5:
        return m > e
    if ct == 6:
        return m == _wrap(vt, e + s)
    if ct == 7:
        return m < e
    if ct == 8:
        return m == _wrap(vt, e - s)
    if ct == 9:
        return m != e
    return m == e                                          # ct == 10
