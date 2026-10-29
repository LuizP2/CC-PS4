"""Formato de fio do ps4debug (jogolden, protocolo "1.2").

Tudo aqui foi conferido no código-fonte do payload (repositório jogolden/ps4debug):
debugger/include/protocol.h, debug.h, proc.h, kdbg.h e debugger/source/server.c,
debug.c, proc.c, console.c, kern.c. As armadilhas de comportamento que o cliente
precisa respeitar estão documentadas em docs/protocolo-ps4debug.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

PACKET_MAGIC = 0xFFAABBCC
BROADCAST_MAGIC = 0xFFFFAAAA
SERVER_PORT = 744  # server.h
DEBUG_PORT = 755  # debug.h: o PS4 conecta de volta no PC nesta porta
BROADCAST_PORT = 1010  # server.h
NET_MAX_LENGTH = 8192  # net.h
MAX_BREAKPOINTS = 30  # protocol.h
MAX_WATCHPOINTS = 4  # protocol.h


class Cmd(IntEnum):
    VERSION = 0xBD000001
    PROC_LIST = 0xBDAA0001
    PROC_READ = 0xBDAA0002
    PROC_WRITE = 0xBDAA0003
    PROC_MAPS = 0xBDAA0004
    PROC_INFO = 0xBDAA000A
    DEBUG_ATTACH = 0xBDBB0001
    DEBUG_DETACH = 0xBDBB0002
    DEBUG_BREAKPT = 0xBDBB0003
    DEBUG_WATCHPT = 0xBDBB0004
    DEBUG_THREADS = 0xBDBB0005
    DEBUG_STOPTHR = 0xBDBB0006
    DEBUG_RESUMETHR = 0xBDBB0007
    DEBUG_GETREGS = 0xBDBB0008
    DEBUG_GETDBGREGS = 0xBDBB000C
    DEBUG_STOPGO = 0xBDBB0010
    DEBUG_THRINFO = 0xBDBB0011
    DEBUG_SINGLESTEP = 0xBDBB0012
    KERN_BASE = 0xBDCC0001
    KERN_READ = 0xBDCC0002
    CONSOLE_NOTIFY = 0xBDDD0004


class Status(IntEnum):
    SUCCESS = 0x80000000
    ERROR = 0xF0000001
    TOO_MUCH_DATA = 0xF0000002
    DATA_NULL = 0xF0000003
    ALREADY_DEBUG = 0xF0000004
    INVALID_INDEX = 0xF0000005


STATUS_TEXT = {
    Status.ERROR: "erro genérico do ps4debug",
    Status.TOO_MUCH_DATA: "dados demais",
    Status.DATA_NULL: "dados nulos ou falta de memória no payload",
    Status.ALREADY_DEBUG: "o ps4debug já está numa sessão de debug",
    Status.INVALID_INDEX: "índice inválido",
}

# Estruturas empacotadas, little-endian (sizes nos comentários batem com os #define do C).
HEADER = struct.Struct("<III")  # magic, cmd, datalen (CMD_PACKET_SIZE 12)
PID = struct.Struct("<I")
PROC_RW = struct.Struct("<IQI")  # pid, address, length (16)
PROC_LIST_ENTRY = struct.Struct("<32si")  # p_comm[32], pid (36)
MAP_ENTRY = struct.Struct("<32sQQQH")  # name[32], start, end, offset, prot (58)
PROC_INFO = struct.Struct("<I40s64s16s64s")  # pid, name, path, titleid, contentid (188)
BREAKPT = struct.Struct("<IIQ")  # index, enabled, address (16)
WATCHPT = struct.Struct("<IIIIQ")  # index, enabled, length, breaktype, address (24)
THRINFO = struct.Struct("<II32s")  # lwpid, priority, name[32] (40)
KERN_READ = struct.Struct("<QI")  # address, length (12)
NOTIFY = struct.Struct("<II")  # messageType, length (8)
U32 = struct.Struct("<I")
I32 = struct.Struct("<i")
U64 = struct.Struct("<Q")

# struct __reg64 (debug.h), 176 bytes
REG_NAMES = (
    "r15", "r14", "r13", "r12", "r11", "r10", "r9", "r8", "rdi", "rsi", "rbp", "rbx",
    "rdx", "rcx", "rax", "trapno", "fs", "gs", "err", "es", "ds", "rip", "cs", "rflags",
    "rsp", "ss",
)
REGS = struct.Struct("<15QIHHIHH5Q")
DBREGS = struct.Struct("<16Q")  # struct __dbreg64, 128 bytes
FPU_SIZE = 832  # struct savefpu_ymm
INTERRUPT_HEAD = struct.Struct("<II40s")
INTERRUPT_SIZE = 0x4A0  # DEBUG_INTERRUPT_PACKET_SIZE
assert INTERRUPT_HEAD.size + REGS.size + FPU_SIZE + DBREGS.size == INTERRUPT_SIZE
assert REGS.size == 176 and MAP_ENTRY.size == 58 and PROC_INFO.size == 188

# Codificação de DR7 (debug.h: DBREG_DR7_LEN_* e DBREG_DR7_EXEC/WRONLY/RDWR)
DR7_LEN = {1: 0x00, 2: 0x01, 4: 0x03, 8: 0x02}
DR7_TYPE = {"exec": 0x00, "write": 0x01, "rw": 0x03}

# Sinais do FreeBSD (o PS4 é FreeBSD 9): atenção, SIGSTOP é 17 e SIGBUS é 10.
SIGNALS = {
    1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL", 5: "SIGTRAP", 6: "SIGABRT",
    7: "SIGEMT", 8: "SIGFPE", 9: "SIGKILL", 10: "SIGBUS", 11: "SIGSEGV", 12: "SIGSYS",
    13: "SIGPIPE", 14: "SIGALRM", 15: "SIGTERM", 17: "SIGSTOP", 19: "SIGCONT",
}
SIGTRAP = 5
CRASH_SIGNALS = {4, 6, 8, 10, 11, 12}

PROT_READ, PROT_WRITE, PROT_EXEC = 1, 2, 4


def cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def prot_str(prot: int) -> str:
    s = ("r" if prot & PROT_READ else "-") + ("w" if prot & PROT_WRITE else "-") + ("x" if prot & PROT_EXEC else "-")
    extra = prot & ~0x7
    return f"{s}+{extra:#x}" if extra else s


@dataclass(frozen=True)
class MapEntry:
    name: str
    start: int
    end: int
    offset: int
    prot: int

    @property
    def size(self) -> int:
        return self.end - self.start

    def contains(self, address: int) -> bool:
        return self.start <= address < self.end


def parse_regs(raw: bytes) -> dict[str, int]:
    return dict(zip(REG_NAMES, REGS.unpack(raw[: REGS.size])))


def pack_regs(regs: dict[str, int]) -> bytes:
    return REGS.pack(*(regs.get(n, 0) for n in REG_NAMES))


@dataclass
class Interrupt:
    lwpid: int
    status: int
    thread_name: str
    regs: dict[str, int]
    dbregs: tuple[int, ...]


def parse_interrupt(raw: bytes) -> Interrupt:
    if len(raw) != INTERRUPT_SIZE:
        raise ValueError(f"pacote de interrupção com {len(raw)} bytes (esperado {INTERRUPT_SIZE})")
    lwpid, status, name = INTERRUPT_HEAD.unpack_from(raw, 0)
    regs = parse_regs(raw[INTERRUPT_HEAD.size :])
    dbregs = DBREGS.unpack_from(raw, INTERRUPT_HEAD.size + REGS.size + FPU_SIZE)
    return Interrupt(lwpid, status, cstr(name), regs, dbregs)


def wait_status(status: int) -> tuple[str, int]:
    """Decodifica o status do wait4() do FreeBSD: ("stopped"|"exited"|"signaled", número)."""
    if status & 0xFF == 0x7F:
        return "stopped", (status >> 8) & 0xFF
    if status & 0x7F == 0:
        return "exited", (status >> 8) & 0xFF
    return "signaled", status & 0x7F


def signal_name(sig: int) -> str:
    return SIGNALS.get(sig, f"SIG{sig}")
