#!/usr/bin/env python3
"""Gera build/offsets.txt a partir do ELF do CCPS4 Crash Test (só stdlib).

Para cada símbolo-chave imprime o offset relativo à base da imagem:

    offset = st_value - menor p_vaddr dos segmentos PT_LOAD

Com o link.x do OpenOrbis o .text começa no endereço de link 0, então a base de
link é 0 e o offset é o próprio st_value. Em tempo de execução:

    endereço_runtime = base_do_módulo(eboot.bin) + offset

Uso:
    gen_offsets.py <arquivo.elf> [--disasm <saida do llvm-objdump de crash_here>]
"""
from __future__ import annotations

import argparse
import re
import struct
import sys

KEY_SYMBOLS = ["crash_here", "g_watch_counter", "g_subtitle_utf8", "g_subtitle_utf16",
               "main", "g_heap_copy", "g_heap_copy16", "__text_start"]

PT_LOAD = 1
SHT_SYMTAB = 2


def parse_elf(path: str):
    data = open(path, "rb").read()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        sys.exit(f"{path}: não é um ELF64 little-endian")
    (e_type, e_machine, _ver, _entry, e_phoff, e_shoff, _flags, _ehsize,
     e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx) = struct.unpack_from(
        "<HHIQQQIHHHHHH", data, 16)

    loads = []
    for i in range(e_phnum):
        p_type, p_flags, p_offset, p_vaddr, _paddr, p_filesz, p_memsz, _align = struct.unpack_from(
            "<IIQQQQQQ", data, e_phoff + i * e_phentsize)
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_memsz, p_flags))

    sections = []
    for i in range(e_shnum):
        (sh_name, sh_type, _sh_flags, sh_addr, sh_offset, sh_size, sh_link, _info, _align,
         sh_entsize) = struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + i * e_shentsize)
        sections.append(dict(name_off=sh_name, type=sh_type, addr=sh_addr, offset=sh_offset,
                             size=sh_size, link=sh_link, entsize=sh_entsize))
    shstr = sections[e_shstrndx]
    for s in sections:
        end = data.index(b"\0", shstr["offset"] + s["name_off"])
        s["name"] = data[shstr["offset"] + s["name_off"]:end].decode()

    symbols = {}
    for s in sections:
        if s["type"] != SHT_SYMTAB:
            continue
        strtab = sections[s["link"]]
        for j in range(s["size"] // 24):
            st_name, st_info, _other, st_shndx, st_value, st_size = struct.unpack_from(
                "<IBBHQQ", data, s["offset"] + j * 24)
            end = data.index(b"\0", strtab["offset"] + st_name)
            name = data[strtab["offset"] + st_name:end].decode(errors="replace")
            if name and st_shndx != 0:
                sec = sections[st_shndx]["name"] if st_shndx < len(sections) else "ABS"
                symbols.setdefault(name, (st_value, st_size, sec))
    return e_type, e_machine, loads, symbols


def find_fault_insn(disasm_path: str) -> tuple[int, str] | None:
    """Acha, no disassembly (sintaxe Intel) de crash_here, a leitura de 32 bits de p->value.

    É a primeira carga "mov e??, dword ptr [reg...]" cuja base não é rbp/rsp (com -O0
    o compilador soma 8 ao ponteiro antes: "add rax, 0x8" e depois "mov eax, dword ptr [rax]";
    com otimização vira "mov eax, dword ptr [rdi + 0x8]").
    """
    pat = re.compile(r"^\s*([0-9a-f]+):\s+(mov\s+e\w+,\s+dword ptr \[(r\w+)[^\]]*\])", re.I)
    for line in open(disasm_path, encoding="utf-8", errors="replace"):
        m = pat.match(line)
        if m and m.group(3).lower() not in ("rbp", "rsp", "rip"):
            return int(m.group(1), 16), " ".join(m.group(2).split())
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("--disasm")
    args = ap.parse_args()

    e_type, e_machine, loads, symbols = parse_elf(args.elf)
    if e_machine != 62:
        sys.exit(f"e_machine={e_machine}, esperado 62 (x86-64)")
    base = min(v for v, _, _ in loads)

    out = []
    out.append("# CCPS4 Crash Test — offsets relativos à base da imagem (eboot.bin)")
    out.append(f"# ELF: {args.elf} (e_type={e_type}, {'PIE/ET_DYN' if e_type == 3 else 'ET_EXEC'})")
    out.append(f"# base de link (menor p_vaddr PT_LOAD) = 0x{base:x}")
    ts = symbols.get("__text_start")
    if ts:
        out.append(f"# __text_start (link.x) = 0x{ts[0]:x}  -> em runtime, &__text_start == base do eboot.bin")
    out.append("# endereço_runtime = base_do_módulo + offset ;  module_offset = rip - base_do_módulo")
    out.append("# breakpoint: set_breakpoint(\"eboot.bin+0x<offset>\")")
    out.append("#")
    out.append(f"# {'símbolo':<18} {'offset':<12} {'tamanho':<9} {'seção':<10} alvo")
    for name in KEY_SYMBOLS:
        if name not in symbols:
            out.append(f"# AVISO: símbolo {name} não encontrado")
            continue
        value, size, sec = symbols[name]
        off = value - base
        out.append(f"{name:<20} 0x{off:08x}  0x{size:<7x} {sec:<10} eboot.bin+0x{off:x}")

    if args.disasm and "crash_here" in symbols:
        fault = find_fault_insn(args.disasm)
        if fault:
            addr, insn = fault
            off = addr - base
            rel = addr - symbols["crash_here"][0]
            out.append(f"{'crash_fault_insn':<20} 0x{off:08x}  {'-':<9} {'.text':<10} "
                       f"eboot.bin+0x{off:x}  (crash_here+0x{rel:x}: {insn})")
        else:
            out.append("# AVISO: não achei a instrução de leitura [reg + 0x8] em crash_here")

    out.append("#")
    out.append("# segmentos PT_LOAD (vaddr, memsz, flags R=4 W=2 X=1):")
    for vaddr, memsz, flags in loads:
        out.append(f"#   0x{vaddr:08x}  0x{memsz:08x}  {flags}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
