"""Testes das partes puras: protocolo, endereços, snapshot e ELF."""

import shutil
import struct
import subprocess

import pytest

from ps4_mcp import protocol as p
from ps4_mcp.client import describe_address, module_base, parse_address, unmapped_warning
from ps4_mcp.elfdump import EHDR, PHDR, build_elf
from ps4_mcp.snapshot import disasm, guess_fault, stack_code_pointers

MAPS = [
    p.MapEntry("eboot.bin", 0x400000, 0x480000, 0, 5),
    p.MapEntry("eboot.bin", 0x480000, 0x4A0000, 0, 3),
    p.MapEntry("libc.sprx", 0x800000, 0x880000, 0, 5),
    p.MapEntry("heap", 0x2000000, 0x2100000, 0, 3),
]


def test_struct_sizes_match_c_defines():
    assert p.HEADER.size == 12
    assert p.PROC_RW.size == 16
    assert p.PROC_LIST_ENTRY.size == 36
    assert p.MAP_ENTRY.size == 58
    assert p.PROC_INFO.size == 188
    assert p.BREAKPT.size == 16
    assert p.WATCHPT.size == 24
    assert p.THRINFO.size == 40
    assert p.REGS.size == 176
    assert p.INTERRUPT_SIZE == 0x4A0


def test_parse_interrupt_layout():
    regs = {name: i + 1 for i, name in enumerate(p.REG_NAMES)}
    regs |= {"rip": 0x401234, "rsp": 0x7FFF0000}
    dbregs = [0] * 16
    dbregs[6] = 0b0010
    raw = (p.INTERRUPT_HEAD.pack(77, (11 << 8) | 0x7F, b"GameThread\0")
           + p.pack_regs(regs) + bytes(p.FPU_SIZE) + p.DBREGS.pack(*dbregs))
    intr = p.parse_interrupt(raw)
    assert intr.lwpid == 77 and intr.thread_name == "GameThread"
    assert intr.regs["rip"] == 0x401234 and intr.regs["r15"] == 1 and intr.regs["rax"] == 15
    assert intr.dbregs[6] == 2
    assert p.wait_status(intr.status) == ("stopped", 11)
    with pytest.raises(ValueError):
        p.parse_interrupt(raw[:-1])


def test_wait_status_variants():
    assert p.wait_status((5 << 8) | 0x7F) == ("stopped", 5)
    assert p.wait_status(3 << 8) == ("exited", 3)
    assert p.wait_status(9) == ("signaled", 9)
    assert p.signal_name(17) == "SIGSTOP"  # FreeBSD, não Linux


def test_address_expressions():
    assert parse_address("0x401000") == 0x401000
    assert parse_address("4198400") == 4198400
    assert parse_address("eboot.bin+0x1234", MAPS) == 0x401234
    assert parse_address("libc.sprx", MAPS) == 0x800000
    assert module_base(MAPS, "EBOOT.BIN") == 0x400000
    assert describe_address(MAPS, 0x481000) == "eboot.bin+0x81000"
    assert "não mapeado" in describe_address(MAPS, 0x10)
    with pytest.raises(ValueError, match="não encontrado"):
        parse_address("nada.sprx+0x10", MAPS)
    with pytest.raises(ValueError):
        parse_address("eboot.bin+0x10")


def test_unmapped_warning():
    assert unmapped_warning(MAPS, 0x400000, 0x100) is None
    assert unmapped_warning(MAPS, 0x49FFF0, 0x20) is not None  # atravessa o fim do mapa
    assert unmapped_warning(MAPS, 0x479FF0, 0x20) is None  # regiões contíguas


def test_guess_fault_null_field_access():
    code = bytes.fromhex("8b4008") + b"\xc3"  # mov eax, dword ptr [rax + 8]
    fault = guess_fault(code, 0x401000, {"rax": 0, "rip": 0x401000})
    assert fault["address"] == "0x8"
    assert "ponteiro nulo" in fault["note"]


def test_guess_fault_rip_relative_and_index():
    code = bytes.fromhex("488b1500010000")  # mov rdx, qword ptr [rip + 0x100]
    assert guess_fault(code, 0x401000, {})["address"] == hex(0x401000 + 7 + 0x100)
    code = bytes.fromhex("8b0c88")  # mov ecx, dword ptr [rax + rcx*4]
    fault = guess_fault(code, 0x401000, {"rax": 0x2000000, "rcx": 3})
    assert fault["address"] == hex(0x2000000 + 12)


def test_stack_code_pointers():
    stack = struct.pack("<4Q", 0x1234, 0x401500, 0x2000010, 0x800100)
    ptrs = stack_code_pointers(stack, 0x7FFF0000, MAPS)
    assert [x["where"] for x in ptrs] == ["eboot.bin+0x1500", "libc.sprx+0x100"]


def test_disasm():
    assert disasm(bytes.fromhex("554889e5c3"), 0x401000)[1] == "0x401001: mov rbp, rsp"


def test_build_elf_layout(tmp_path):
    code = bytes.fromhex("554889e58b4008c3") + bytes(0x1000 - 8)
    data = b"CCPS4" + bytes(0x800)
    elf = build_elf([(0x480000, data, 3), (0x400000, code, 5)])
    ident, etype, machine, *_rest = EHDR.unpack_from(elf)
    assert ident[:4] == b"\x7fELF" and etype == 2 and machine == 62
    phnum = EHDR.unpack_from(elf)[10]
    phdrs = [PHDR.unpack_from(elf, EHDR.size + i * PHDR.size) for i in range(phnum)]
    assert [ph[3] for ph in phdrs] == [0x400000, 0x480000]  # ordenados por endereço
    for _type, flags, offset, vaddr, _paddr, filesz, _memsz, align in phdrs:
        assert offset % align == vaddr % align
    assert phdrs[0][1] == 5 and phdrs[1][1] == 6  # R+X, R+W
    assert elf[phdrs[0][2] : phdrs[0][2] + 8] == code[:8]
    if shutil.which("readelf"):
        path = tmp_path / "dump.elf"
        path.write_bytes(elf)
        out = subprocess.run(["readelf", "-lW", str(path)], capture_output=True, text=True).stdout
        assert "0x0000000000400000" in out and "R E" in out
