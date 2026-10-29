"""Leitura e escrita do param.sfo (formato PSF) do PS4.

Layout (tudo little-endian):
- cabeçalho de 20 bytes: magic "\\0PSF", versão u32, início da tabela de chaves u32,
  início da tabela de dados u32, número de entradas u32;
- índice com uma entrada de 16 bytes por chave: key_offset u16, data_fmt u16,
  data_len u32, data_max_len u32, data_offset u32;
- tabela de chaves (ASCII terminado em NUL, em ordem alfabética, alinhada a 4 bytes);
- tabela de dados (cada valor ocupa data_max_len bytes, múltiplo de 4).
"""

from __future__ import annotations

import struct

__all__ = ["parse_sfo", "build_sfo", "FMT_UTF8_SPECIAL", "FMT_UTF8", "FMT_INT32"]

MAGIC = b"\0PSF"
VERSION = 0x0101
FMT_UTF8_SPECIAL = 0x0004  # UTF-8 sem terminador (ex.: dados binários de save)
FMT_UTF8 = 0x0204  # UTF-8 terminado em NUL
FMT_INT32 = 0x0404  # inteiro sem sinal (4 bytes; alguns saves usam 8)

_HEADER = struct.Struct("<4sIIII")
_ENTRY = struct.Struct("<HHIII")

# espaço reservado (data_max_len) que as ferramentas da Sony usam nas chaves comuns
_RESERVED = {
    "APP_VER": 8,
    "CATEGORY": 4,
    "CONTENT_ID": 48,
    "FORMAT": 4,
    "PUBTOOLINFO": 512,
    "TITLE": 128,
    "TITLE_ID": 12,
    "VERSION": 8,
}


def _align4(n: int) -> int:
    return (n + 3) & ~3


def _reserved_len(key: str, needed: int) -> int:
    known = _RESERVED.get(key, 0)
    if not known and key.startswith("TITLE_") and key[6:].isdigit():
        known = 128  # títulos localizados TITLE_00..TITLE_30
    elif not known and key.startswith("SERVICE_ID_ADDCONT_ADD_"):
        known = 20
    return max(known, _align4(needed))


def _decode_value(fmt: int, raw: bytes) -> str | int:
    if fmt == FMT_INT32:
        return int.from_bytes(raw, "little")
    if fmt == FMT_UTF8:
        return raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    if fmt == FMT_UTF8_SPECIAL:
        try:
            text = raw.rstrip(b"\0").decode("utf-8")
        except UnicodeDecodeError:
            return raw.hex()
        return text if text.isprintable() else raw.hex()
    return raw.hex()  # formato desconhecido: devolve os bytes em hex


def parse_sfo(data: bytes) -> dict[str, str | int]:
    """Lê um param.sfo e devolve {chave: valor}.

    Strings UTF-8 viram str; inteiros viram int; valores binários (formato 0x0004 não
    textual ou formato desconhecido) viram a string hex dos bytes.
    Levanta ValueError se o magic estiver errado ou o arquivo estiver truncado/corrompido.
    """
    data = bytes(data)
    if len(data) < _HEADER.size:
        raise ValueError(f"param.sfo truncado: {len(data)} bytes (só o cabeçalho tem {_HEADER.size}).")
    magic, _version, key_start, data_start, count = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError(f"Não é um param.sfo: magic {magic!r} (esperado {MAGIC!r}).")
    index_end = _HEADER.size + count * _ENTRY.size
    if index_end > len(data) or key_start > len(data) or data_start > len(data):
        raise ValueError(
            f"param.sfo truncado ou corrompido: {count} entradas, tabela de chaves em 0x{key_start:X}, "
            f"dados em 0x{data_start:X}, mas o arquivo tem só {len(data)} bytes."
        )
    out: dict[str, str | int] = {}
    for i in range(count):
        key_off, fmt, length, _max_len, data_off = _ENTRY.unpack_from(data, _HEADER.size + i * _ENTRY.size)
        k = key_start + key_off
        end = data.find(b"\0", k)
        if k >= len(data) or end < 0:
            raise ValueError(f"param.sfo corrompido: a chave da entrada {i} está fora do arquivo.")
        key = data[k:end].decode("utf-8", "replace")
        v = data_start + data_off
        raw = data[v : v + length]
        if len(raw) < length:
            raise ValueError(f"param.sfo truncado: o valor de {key!r} passa do fim do arquivo.")
        out[key] = _decode_value(fmt, raw)
    return out


def build_sfo(entries: dict[str, str | int]) -> bytes:
    """Monta um param.sfo a partir de {chave: str|int} (chaves ordenadas, dados alinhados a 4).

    str vira UTF-8 terminado em NUL (0x0204) com o espaço reservado usual da Sony para chaves
    conhecidas (TITLE 128, TITLE_ID 12, CONTENT_ID 48...); int vira 0x0404 (4 bytes, ou 8 se
    não couber em 32 bits). Faz ida e volta com parse_sfo.
    """
    index, keys, values = bytearray(), bytearray(), bytearray()
    for key in sorted(entries):
        value = entries[key]
        if not key or not key.isascii() or "\0" in key:
            raise ValueError(f"Chave inválida para param.sfo: {key!r} (use ASCII sem NUL).")
        if isinstance(value, int):
            if not 0 <= value < 1 << 64:
                raise ValueError(f"Valor de {key!r} fora da faixa de inteiro sem sinal: {value}.")
            size = 4 if value < 1 << 32 else 8
            fmt, length, reserved = FMT_INT32, size, size
            raw = value.to_bytes(size, "little")
        elif isinstance(value, str):
            raw = value.encode("utf-8") + b"\0"
            fmt, length = FMT_UTF8, len(raw)
            reserved = _reserved_len(key, length)
            raw = raw.ljust(reserved, b"\0")
        else:
            raise ValueError(f"Valor de {key!r} deve ser str ou int, não {type(value).__name__}.")
        index += _ENTRY.pack(len(keys), fmt, length, reserved, len(values))
        keys += key.encode("ascii") + b"\0"
        values += raw
    keys += b"\0" * (-len(keys) % 4)
    key_start = _HEADER.size + len(index)
    data_start = key_start + len(keys)
    header = _HEADER.pack(MAGIC, VERSION, key_start, data_start, len(entries))
    return header + bytes(index) + bytes(keys) + bytes(values)
