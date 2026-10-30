"""Troca strings de um .locres (v1) e empacota num pak UE4 v4 sem criptografia.
Uso: kh3_mod.py <locres_original> <caminho_no_pak> <saida.pak> <indice>=<texto novo> [...]"""
import os
from pathlib import Path
WORK = Path(__file__).resolve().parents[2] / "kh3_work"  # fora do git: chave e arquivos do jogo
import hashlib, struct, sys

MAGIC = bytes.fromhex("0E147475674A03FC4A15909DC3377F1B")


def read_locres(b: bytes):
    assert b[:16] == MAGIC and b[16] == 1, "só locres v1"
    pos = [25]
    def u(f):
        v = struct.unpack_from(f, b, pos[0]); pos[0] += struct.calcsize(f); return v
    def fs():
        (n,) = u("<i")
        if n < 0:
            s = b[pos[0]:pos[0] - 2 * n].decode("utf-16le"); pos[0] += -2 * n
        else:
            s = b[pos[0]:pos[0] + n].decode("latin-1"); pos[0] += n
        return s[:-1]  # tira o \0
    (off,) = struct.unpack_from("<q", b, 17)
    (nns,) = u("<I")
    table = []
    for _ in range(nns):
        ns = fs(); (nk,) = u("<I")
        table.append((ns, [(fs(), *u("<Ii")) for _ in range(nk)]))
    pos[0] = off
    (n,) = u("<i")
    return table, [fs() for _ in range(n)]


def fstr(s: str, utf16: bool = False) -> bytes:
    if s.isascii() and not utf16:
        return struct.pack("<i", len(s) + 1) + s.encode() + b"\0"
    return struct.pack("<i", -(len(s) + 1)) + s.encode("utf-16le") + b"\0\0"


def write_locres(table, strs, utf16: bool) -> bytes:
    """utf16=True grava todo FString em UTF-16, como o KH3 faz; False usa ANSI quando o texto é ASCII."""
    keys = struct.pack("<I", len(table))
    for ns, entries in table:
        keys += fstr(ns, utf16) + struct.pack("<I", len(entries))
        for k, h, i in entries:
            keys += fstr(k, utf16) + struct.pack("<Ii", h, i)
    arr = struct.pack("<i", len(strs)) + b"".join(fstr(s, utf16) for s in strs)
    return MAGIC + b"\x01" + struct.pack("<q", 25 + len(keys)) + keys + arr


def pak_v4(name: str, data: bytes) -> bytes:
    sha = hashlib.sha1(data).digest()
    entry = lambda off: struct.pack("<qqqi", off, len(data), len(data), 0) + sha + struct.pack("<BI", 0, 0)
    body = entry(0) + data  # cabeçalho repetido antes dos dados (offset 0, como o UnrealPak grava)
    index = fstr("../../../") + struct.pack("<i", 1) + fstr(name) + entry(0)
    footer = struct.pack("<BIIQQ", 0, 0x5A6F12E1, 4, len(body), len(index)) + hashlib.sha1(index).digest()
    return body + index + footer


if __name__ == "__main__":
    src, inner, out, *edits = sys.argv[1:]
    orig = open(src, "rb").read()
    table, strs = read_locres(orig)
    for utf16 in (True, False):  # descobre como o original grava os FStrings
        if write_locres(table, strs, utf16) == orig:
            break
    else:
        sys.exit("a reescrita sem mudanças não bate com o original; não vou gerar o mod")
    for e in edits:
        i, text = e.split("=", 1)
        print(f"[{i}] {strs[int(i)]!r} -> {text!r}")
        strs[int(i)] = text
    open(out, "wb").write(pak_v4(inner, write_locres(table, strs, utf16)))
    print(f"{out}: {len(open(out, 'rb').read())} bytes")
