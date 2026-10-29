import struct

import pytest

from ps4_mcp.sfo import build_sfo, parse_sfo

# param.sfo montado à mão, no layout das ferramentas da Sony:
# 4 entradas, índice em 0x14, chaves em 0x54 (35 bytes + 1 de alinhamento), dados em 0x78.
KNOWN_GOOD = (
    bytes.fromhex("00505346 01010000 54000000 78000000 04000000")  # magic, versão 1.1, tabelas, nº
    # key_off fmt  len      max_len  data_off
    + bytes.fromhex("0000 0404 04000000 04000000 00000000")  # APP_TYPE (int32)
    + bytes.fromhex("0900 0402 06000000 08000000 04000000")  # APP_VER  (utf8, reserva 8)
    + bytes.fromhex("1100 0402 03000000 04000000 0c000000")  # CATEGORY (utf8, reserva 4)
    + bytes.fromhex("1a00 0402 0a000000 0c000000 10000000")  # TITLE_ID (utf8, reserva 12)
    + b"APP_TYPE\0APP_VER\0CATEGORY\0TITLE_ID\0" + b"\0"
    + b"\x01\x00\x00\x00"
    + b"01.00\0\0\0"
    + b"gd\0\0"
    + b"CUSA12345\0\0\0"
)
KNOWN_VALUES = {"APP_TYPE": 1, "APP_VER": "01.00", "CATEGORY": "gd", "TITLE_ID": "CUSA12345"}


def test_parse_known_good_sfo():
    assert len(KNOWN_GOOD) == 0x94
    assert parse_sfo(KNOWN_GOOD) == KNOWN_VALUES


def test_build_matches_known_good_bytes():
    assert build_sfo(KNOWN_VALUES) == KNOWN_GOOD
    assert build_sfo(dict(reversed(KNOWN_VALUES.items()))) == KNOWN_GOOD  # chaves sempre ordenadas


def test_round_trip_and_alignment():
    entries = {
        "TITLE": "Kingdom Hearts III — Olá, ação",
        "TITLE_ID": "CUSA12025",
        "CONTENT_ID": "UP0082-CUSA12025_00-KH3000000000000",
        "TITLE_05": "Título em português",
        "PUBTOOLINFO": "c_date=20190125,sdk_ver=06000000",
        "SYSTEM_VER": 0x06000000,
        "ATTRIBUTE": 0,
        "CUSTOM_KEY": "abc",
        "SAVEDATA_BLOCKS": 0x1_0000_0000,  # não cabe em 32 bits: vira 8 bytes
    }
    raw = build_sfo(entries)
    assert parse_sfo(raw) == entries

    _, _, key_start, data_start, count = struct.unpack_from("<4sIIII", raw)
    assert count == len(entries) and key_start % 4 == 0 and data_start % 4 == 0
    keys, reserved = [], {}
    for i in range(count):
        key_off, fmt, length, max_len, data_off = struct.unpack_from("<HHIII", raw, 0x14 + i * 16)
        key = raw[key_start + key_off : raw.index(b"\0", key_start + key_off)].decode()
        keys.append(key)
        reserved[key] = max_len
        assert max_len % 4 == 0 and data_off % 4 == 0 and length <= max_len
    assert keys == sorted(entries)
    assert reserved["TITLE"] == 128 and reserved["TITLE_05"] == 128 and reserved["CONTENT_ID"] == 48
    assert reserved["PUBTOOLINFO"] == 512 and reserved["SYSTEM_VER"] == 4 and reserved["CUSTOM_KEY"] == 4
    assert reserved["SAVEDATA_BLOCKS"] == 8


def test_parse_special_and_unknown_formats():
    # entrada 0x0004 (utf8 especial, sem NUL) com bytes binários e outra textual
    raw = bytearray(build_sfo({"ACCOUNT_ID": "x" * 7, "SAVEDATA_DIRECTORY": "SAVE0001"}))
    _, _, key_start, data_start, _ = struct.unpack_from("<4sIIII", raw)
    struct.pack_into("<HHI", raw, 0x14, 0, 0x0004, 8)  # ACCOUNT_ID: 8 bytes binários
    raw[data_start : data_start + 8] = bytes.fromhex("1122334455667788")
    struct.pack_into("<HH", raw, 0x14 + 16, 11, 0x0004)  # SAVEDATA_DIRECTORY: texto especial
    parsed = parse_sfo(bytes(raw))
    assert parsed["ACCOUNT_ID"] == "1122334455667788"
    assert parsed["SAVEDATA_DIRECTORY"] == "SAVE0001"


def test_parse_errors():
    with pytest.raises(ValueError, match="truncado"):
        parse_sfo(b"\0PSF")
    with pytest.raises(ValueError, match="magic"):
        parse_sfo(b"\x7fELF" + KNOWN_GOOD[4:])
    with pytest.raises(ValueError, match="truncado"):
        parse_sfo(KNOWN_GOOD[:0x60])  # corta no meio da tabela de chaves
    with pytest.raises(ValueError, match="truncado"):
        parse_sfo(KNOWN_GOOD[:-6])  # corta o valor de TITLE_ID


def test_build_errors():
    with pytest.raises(ValueError):
        build_sfo({"TITLE": 1.5})
    with pytest.raises(ValueError):
        build_sfo({"APP_TYPE": -1})
    with pytest.raises(ValueError):
        build_sfo({"TÍTULO": "x"})
