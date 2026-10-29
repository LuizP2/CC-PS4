"""Run the fake ps4debug server with a demo console.

    uv run python -m tests.fakes.run_fake --port 7440 --debug-port 7550 [--elf path] [--scenario demo]

Interactive commands (stdin): bp N | watch N [rip] | segv [lwpid] | sig N | exit | status | help | quit
"""

from __future__ import annotations

import argparse
import struct
import sys
import time

from tests.fakes.fake_ps4debug import FakePS4Debug, FakeProcess, SIGSEGV

GAME_PID = 87
TITLE_ID = "CUSA12345"
CONTENT_ID = "UP9000-CUSA12345_00-CCPS4DEMO0000001"
CODE_BASE = 0x400000
DATA_BASE = 0x404000
HEAP_BASE = 0x200000000
LIBKERNEL_BASE = 0x800000000
STACK_BASE = 0x7EFFFFF00000
SUBTITLE = "CCPS4 legenda de teste: Olá, ação, coração!"


def _demo_code() -> bytes:
    """increment_counter(): push rbp; mov rbp,rsp; mov eax,[rip+c]; add eax,1; mov [rip+c],eax;
    pop rbp; ret   -- with the counter at DATA_BASE."""
    code = bytearray(b"\x55\x48\x89\xe5")
    code += b"\x8b\x05" + struct.pack("<i", DATA_BASE - (CODE_BASE + 0x0A))
    code += b"\x83\xc0\x01"
    code += b"\x89\x05" + struct.pack("<i", DATA_BASE - (CODE_BASE + 0x13))
    code += b"\x5d\xc3"
    return bytes(code)


def build_demo(fake: FakePS4Debug, elf: str | None = None) -> dict:
    """Populate ``fake`` with system processes and a game. Returns useful addresses."""
    for pid, name in [(0, "kernel"), (1, "mini-syscore.elf"), (25, "SceSysCore.elf"),
                      (46, "SceShellCore"), (48, "SceShellUI"), (52, "SceAvSetting")]:
        p = fake.add_process(pid, name, path=f"/system/{name}")
        if pid:
            p.map("libkernel_sys.sprx", LIBKERNEL_BASE, 0x40000, prot=5)
            p.add_thread(100000 + pid * 10, name[:20], 256)
    game: FakeProcess = fake.add_process(GAME_PID, "eboot.bin", TITLE_ID, CONTENT_ID,
                                         f"/mnt/sandbox/{TITLE_ID}_000/app0/eboot.bin")
    if elf:
        game.load_elf(elf, CODE_BASE)
        e_entry = struct.unpack_from("<Q", open(elf, "rb").read(), 0x18)[0]
        entry = CODE_BASE + e_entry
    else:
        game.map("eboot.bin", CODE_BASE, _demo_code(), prot=5)
        game.map("eboot.bin", DATA_BASE, 0x4000, prot=3)
        entry = CODE_BASE
    utf8 = SUBTITLE.encode("utf-8") + b"\0"
    utf16 = SUBTITLE.encode("utf-16-le") + b"\0\0"
    heap = bytearray(0x100000)
    struct.pack_into("<IIQ", heap, 0x1000, 100, 7, HEAP_BASE + 0x2000)   # struct {hp, level, char *subtitle}
    heap[0x2000:0x2000 + len(utf8)] = utf8
    heap[0x3000:0x3000 + len(utf16)] = utf16
    game.map("SceLibcInternalHeap", HEAP_BASE, bytes(heap), prot=3)
    base_ptr = DATA_BASE + 0x10 if not elf else HEAP_BASE + 0x8
    game.write(base_ptr, struct.pack("<Q", HEAP_BASE + 0x1000))           # base pointer -> struct
    game.map("libkernel.sprx", LIBKERNEL_BASE, 0x40000, prot=5)
    game.map("main stack", STACK_BASE, 0x100000, prot=3)
    game.add_thread(100100, "main", 700, {"rip": entry, "rsp": STACK_BASE + 0xFF000})
    game.add_thread(100101, "RenderThread", 256, {"rip": entry + 0x13})
    game.add_thread(100102, "AudioThread", 700)
    return {"pid": GAME_PID, "entry": entry, "base_ptr": base_ptr, "struct": HEAP_BASE + 0x1000,
            "utf8": HEAP_BASE + 0x2000, "utf16": HEAP_BASE + 0x3000}


def _repl(fake: FakePS4Debug) -> None:
    print("commands: bp N | watch N [rip] | segv [lwpid] | sig N | exit | status | quit", flush=True)
    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        cmd, args = parts[0].lower(), [int(a, 0) for a in parts[1:]]
        try:
            if cmd == "bp":
                fake.hit_breakpoint(args[0] if args else 0)
            elif cmd == "watch":
                fake.hit_watchpoint(args[0] if args else 0, rip=args[1] if len(args) > 1 else 0)
            elif cmd == "segv":
                fake.raise_signal(SIGSEGV, lwpid=args[0] if args else None)
            elif cmd == "sig":
                fake.raise_signal(args[0])
            elif cmd == "exit":
                fake.exit_process()
            elif cmd == "status":
                print(f"alive={fake.alive} attached={fake.attached_pid} g_debugging={fake.g_debugging} "
                      f"state={fake.process_state} bps={fake.breakpoints} wps={fake.watchpoints} "
                      f"clients={fake.connected_clients} crashed={fake.payload_crashed}", flush=True)
                continue
            elif cmd in ("quit", "q"):
                return
            else:
                print("unknown command", flush=True)
                continue
            print("ok", flush=True)
        except (RuntimeError, IndexError, ValueError) as e:
            print(f"error: {e}", flush=True)
    while fake.alive:         # stdin closed: keep serving
        time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7440)
    ap.add_argument("--debug-port", type=int, default=7550)
    ap.add_argument("--broadcast-port", type=int, default=None)
    ap.add_argument("--elf", default=None, help="plain ELF64 to load as eboot.bin at 0x400000")
    ap.add_argument("--scenario", default="demo", choices=["demo"])
    ap.add_argument("--accept-delay", type=float, default=0.0, help="2.0 emulates the real accept loop")
    ap.add_argument("--faithful-sigkill", action="store_true",
                    help="emulate the NULL deref after a SIGKILL stop (server.c:86) as a payload crash")
    a = ap.parse_args(argv)
    fake = FakePS4Debug(a.host, a.port, debug_port=a.debug_port, broadcast_port=a.broadcast_port,
                        accept_delay=a.accept_delay, faithful_sigkill=a.faithful_sigkill)
    info = build_demo(fake, a.elf)
    fake.start()
    print(f"fake ps4debug 1.2 listening on {a.host}:{fake.port}; connect-back port {a.debug_port}"
          + (f"; UDP discovery on {fake.broadcast_port}" if fake.broadcast_port else ""))
    print(f"  game pid {info['pid']} (eboot.bin {TITLE_ID}), code at {info['entry']:#x}, "
          f"pointer chain {info['base_ptr']:#x} -> {info['struct']:#x} (+0x10) -> {info['utf8']:#x}; "
          f"UTF-16LE copy at {info['utf16']:#x}")
    print(f"  client: PS4 host 127.0.0.1 port {fake.port}; listen for the debugger on {a.debug_port} "
          "BEFORE sending ATTACH")
    try:
        _repl(fake)
    except KeyboardInterrupt:
        pass
    finally:
        fake.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
