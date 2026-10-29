"""Snapshot de evento (seção 4.4 do plano): tudo o que o agente precisa numa chamada só."""

from __future__ import annotations

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_MEM

from .client import PS4Debug, PS4DebugError, describe_address, find_map, module_base
from .protocol import PROT_EXEC

GP_REGS = (
    "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp",
    "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "rip", "rflags",
)
STACK_BYTES = 256
CODE_BEFORE, CODE_AFTER = 32, 32

_cs = Cs(CS_ARCH_X86, CS_MODE_64)
_cs.detail = True
_REG32 = {"eax": "rax", "ebx": "rbx", "ecx": "rcx", "edx": "rdx", "esi": "rsi", "edi": "rdi", "ebp": "rbp", "esp": "rsp"}
_REG32 |= {f"r{i}d": f"r{i}" for i in range(8, 16)}


def disasm(code: bytes, address: int, count: int = 16) -> list[str]:
    return [f"{i.address:#x}: {i.mnemonic} {i.op_str}".rstrip() for i in _cs.disasm(code, address, count)]


def _reg_value(name: str, regs: dict[str, int]) -> int | None:
    if name in regs:
        return regs[name]
    if name in _REG32:
        return regs[_REG32[name]] & 0xFFFFFFFF
    return None


def guess_fault(code: bytes, rip: int, regs: dict[str, int]) -> dict | None:
    """Endereço efetivo do primeiro operando de memória da instrução em rip (o ps4debug não
    repassa o siginfo, então o endereço da falha é reconstruído a partir dos registradores)."""
    insn = next(_cs.disasm(code, rip, 1), None)
    if insn is None:
        return None
    for op in insn.operands:
        if op.type != X86_OP_MEM:
            continue
        mem = op.mem
        if mem.segment:  # fs:/gs: (TLS) não dá para calcular sem a base do segmento
            return {"instruction": f"{insn.mnemonic} {insn.op_str}", "address": None, "note": "acesso relativo a segmento (TLS)"}
        parts, value = [], mem.disp
        if mem.base:
            name = insn.reg_name(mem.base)
            base = rip + insn.size if name == "rip" else _reg_value(name, regs)
            if base is None:
                return None
            value += base
            parts.append(f"{name}={base:#x}")
        if mem.index:
            name = insn.reg_name(mem.index)
            idx = _reg_value(name, regs)
            if idx is None:
                return None
            value += idx * mem.scale
            parts.append(f"{name}={idx:#x}*{mem.scale}")
        value &= 0xFFFFFFFFFFFFFFFF
        note = ", ".join(parts + [f"disp={mem.disp:#x}"])
        if value < 0x10000:
            note += " → endereço baixo: ponteiro nulo + deslocamento de campo"
        return {"instruction": f"{insn.mnemonic} {insn.op_str}", "address": hex(value), "note": note}
    return None


def stack_code_pointers(stack: bytes, rsp: int, maps, limit: int = 12) -> list[dict]:
    """Valores na pilha que apontam para código: candidatos a endereço de retorno (backtrace heurístico)."""
    out = []
    for off in range(0, len(stack) - 7, 8):
        value = int.from_bytes(stack[off : off + 8], "little")
        region = find_map(maps, value)
        if region and region.prot & PROT_EXEC:
            out.append({"at": f"rsp+{off:#x}", "value": hex(value), "where": describe_address(maps, value)})
            if len(out) >= limit:
                break
    return out


def build_snapshot(event: dict, client: PS4Debug, breakpoints: dict, recent_logs: list[str]) -> dict:
    pid, regs = event["pid"], event["regs"]
    rip, rsp = regs["rip"], regs["rsp"]
    snap = {k: event[k] for k in ("event", "signal", "pid", "thread_id", "thread_name") if k in event}
    for key in ("breakpoint_id", "watchpoint_id"):
        if key in event:
            snap[key] = event[key]
    snap["rip"] = hex(rip)
    notes = []
    try:
        maps = client.maps(pid)
        if not find_map(maps, rip):
            maps = client.maps(pid, refresh=True)
    except PS4DebugError as e:
        maps = []
        notes.append(f"não consegui ler os mapas do processo: {e}")

    region = find_map(maps, rip)
    if region:
        base = module_base(maps, region.name)
        snap |= {"module": region.name, "module_offset": hex(rip - base), "module_base": hex(base)}
    else:
        snap |= {"module": None, "module_offset": None}
        notes.append("rip fora das regiões mapeadas (salto para endereço inválido?)")
    snap["registers"] = {r: f"{regs[r]:#x}" for r in GP_REGS}

    if find_map(maps, rsp):
        try:
            stack = client.read(pid, rsp, STACK_BYTES)
            snap["stack_bytes"] = stack.hex()
            snap["stack_code_pointers"] = stack_code_pointers(stack, rsp, maps)
        except PS4DebugError as e:
            notes.append(f"falha ao ler a pilha: {e}")

    if region:
        start = max(region.start, rip - CODE_BEFORE)
        try:
            code = bytearray(client.read(pid, start, (rip - start) + CODE_AFTER))
            for bp in breakpoints.values():  # mostra o código original, sem os 0xCC armados
                if start <= bp["address"] < start + len(code):
                    code[bp["address"] - start] = bp["original"]
            snap["code_start"] = hex(start)
            snap["code_bytes"] = code.hex()
            at_rip = bytes(code[rip - start :])
            snap["disasm"] = disasm(at_rip, rip, 6)
            if event["event"] == "crash":
                fault = guess_fault(at_rip, rip, regs)
                if fault:
                    snap["fault_guess"] = fault
        except PS4DebugError as e:
            notes.append(f"falha ao ler o código: {e}")

    if event["event"] == "breakpoint":
        notes.append("registradores de ANTES da instrução do breakpoint; o ps4debug já a executou internamente (single-step) e rearmou o 0xCC")
    if event["event"] == "watchpoint":
        notes.append("watchpoint é trap pós-execução: rip aponta para a instrução SEGUINTE ao acesso")
    notes.append("processo pausado: use resume() para continuar, step() para avançar uma instrução")
    snap["notes"] = notes
    snap["recent_logs"] = recent_logs
    return snap
