import os
import random
import struct

import pytest

from ps4_mcp import text
from ps4_mcp.text import (
    compare_bytes,
    decode_text,
    encode_needle,
    extract_strings,
    find_all,
    hexdump,
    normalize_encoding,
    search_files,
    summarize_bytes,
)

# --- hexdump / codificações -------------------------------------------------------------


def test_hexdump_format():
    data = b"ABC\x00\x01" + bytes(range(0x41, 0x4F))
    lines = hexdump(data, base=0x1000).splitlines()
    assert lines[0] == "0000000000001000  41 42 43 00 01 41 42 43 44 45 46 47 48 49 4a 4b  ABC..ABCDEFGHIJK"
    assert lines[1] == "0000000000001010  " + "4c 4d 4e".ljust(47) + "  LMN"
    assert hexdump(b"") == ""
    assert hexdump(b"\xff" * 4, width=2).splitlines() == [
        "0000000000000000  ff ff  ..",
        "0000000000000002  ff ff  ..",
    ]


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("utf8", "utf-8"), ("UTF-8", "utf-8"),
        ("utf16", "utf-16le"), ("utf-16", "utf-16le"), ("utf16le", "utf-16le"), ("UTF-16LE", "utf-16le"),
        ("utf16be", "utf-16be"), ("utf-16be", "utf-16be"),
        ("sjis", "shift_jis"), ("shift-jis", "shift_jis"), ("shift_jis", "shift_jis"), ("cp932", "shift_jis"),
        ("latin1", "latin-1"), ("latin-1", "latin-1"), ("iso-8859-1", "latin-1"),
        ("ascii", "ascii"),
    ],
)
def test_normalize_encoding_aliases(alias, expected):
    assert normalize_encoding(alias) == expected


def test_normalize_encoding_rejects_unknown():
    with pytest.raises(ValueError, match="Codificação desconhecida.*utf-8.*shift_jis"):
        normalize_encoding("ebcdic")


# --- decode_text ----------------------------------------------------------------------


def test_decode_utf16_portuguese_with_controls_and_nul():
    data = "Olá, ação\nFim\t!".encode("utf-16le") + b"\x00\x00" + "lixo".encode("utf-16le")
    assert decode_text(data, "utf16", stop_at_nul=True) == "Olá, ação<000A>Fim<0009>!"
    assert decode_text(data, "utf-16le") == "Olá, ação<000A>Fim<0009>!<0000>lixo"


def test_decode_utf16_nul_only_counts_at_even_offset():
    data = "AĀB".encode("utf-16le")  # 41 00 00 01 42 00: bytes 00 00 no offset ímpar 1
    assert data[1:3] == b"\x00\x00"
    assert decode_text(data, "utf16", stop_at_nul=True) == "AĀB"


def test_decode_utf16_undecodable_units():
    assert decode_text("Oi".encode("utf-16le") + b"\x41", "utf16") == "Oi<?41>"  # byte ímpar sobrando
    lone_surrogate = b"\x00\xd8" + "A".encode("utf-16le")
    assert decode_text(lone_surrogate, "utf-16le") == "<?00><?D8>A"


def test_decode_utf16be():
    assert decode_text("ação\n".encode("utf-16be"), "utf16be") == "ação<000A>"


def test_decode_utf8_controls_c1_del_and_invalid_bytes():
    data = b"Ol\xc3\xa1\x1b[1m\x7f\xc2\x85\xff!\n"
    assert decode_text(data, "utf8") == "Olá<1B>[1m<7F><85><?FF>!<0A>"


def test_decode_other_encodings():
    assert decode_text(b"a\x85b\x00c", "latin1", stop_at_nul=True) == "a<85>b"
    assert decode_text(b"a\x85b\x00c", "latin1") == "a<85>b<00>c"
    assert decode_text("テスト\n".encode("shift_jis") + b"\x00resto", "sjis", stop_at_nul=True) == "テスト<0A>"
    assert decode_text(b"abc\xe9", "ascii") == "abc<?E9>"
    assert decode_text("x".encode(), "utf-8") == "<E000>x"  # uso privado (ícones de botão)


def test_encode_needle_and_find_all():
    assert encode_needle("ação", "utf16") == "ação".encode("utf-16le")  # sem BOM
    assert encode_needle("テスト", "sjis") == "テスト".encode("shift_jis")
    with pytest.raises(ValueError, match="ascii"):
        encode_needle("ação", "ascii")
    assert find_all(b"aaaa", b"aa") == [0, 1, 2]
    assert find_all(b"aaaa", b"aa", limit=2) == [0, 1]
    assert find_all(b"abc", b"") == []
    assert find_all(b"abc", b"x") == []


# --- strings / resumo -----------------------------------------------------------------


def test_extract_strings_ascii_and_utf16():
    data = b"\x00\x01Hello World\x00\x00" + "Olá mundo".encode("utf-16le") + b"\xff\xfeabc\x00ZZZZZZ"
    assert extract_strings(data, base=0x1000) == [
        {"address": 0x1002, "encoding": "ascii", "text": "Hello World"},
        {"address": 0x100F, "encoding": "utf-16le", "text": "Olá mundo"},
        {"address": 0x1027, "encoding": "ascii", "text": "ZZZZZZ"},
    ]
    assert [s["text"] for s in extract_strings(data, max_count=1)] == ["Hello World"]
    assert [s["text"] for s in extract_strings(data, min_len=7)] == ["Hello World", "Olá mundo"]


def test_extract_strings_clips_huge_runs():
    (s,) = extract_strings(b"A" * 5000)
    assert s["text"] == "A" * 256 + "…"


def test_summarize_bytes_stats():
    s = summarize_bytes(bytes(range(256)) * 4)
    assert s["size"] == 1024
    assert s["entropy"] == 8.0
    assert s["zero_ratio"] == round(4 / 1024, 3)
    assert s["printable_ratio"] == round(98 / 256, 3)  # 0x20-0x7E + \t \n \r
    assert len(s["head_hexdump"].splitlines()) == 16

    z = summarize_bytes(bytes(100))
    assert (z["entropy"], z["zero_ratio"], z["printable_ratio"], z["strings"]) == (0.0, 1.0, 0.0, [])

    empty = summarize_bytes(b"")
    assert (empty["size"], empty["entropy"], empty["pointer_like"], empty["head_hexdump"]) == (0, 0.0, 0, "")


def test_summarize_bytes_pointers_and_strings():
    blob = struct.pack("<4Q", 0x7FFF_1234_5678, 0x1_0000, 0xFFFF, 0x8000_0000_0000) + b"Some text here!\x00"
    s = summarize_bytes(blob, base=0x2000)
    assert s["pointer_like"] == 2  # 0xFFFF e 0x800000000000 ficam fora da faixa
    assert s["strings"] == [{"address": 0x2020, "encoding": "ascii", "text": "Some text here!"}]
    assert s["head_hexdump"].startswith("0000000000002000  ")


def test_summarize_bytes_pointer_alignment_follows_address():
    blob = b"\x00" * 4 + struct.pack("<Q", 0x7F12_3456_7890) + b"\x00" * 4
    assert summarize_bytes(blob, base=0x2004)["pointer_like"] == 1  # ponteiro em 0x2008 (alinhado)
    assert summarize_bytes(blob, base=0x2000)["pointer_like"] == 0  # leitura desalinhada


def test_summarize_bytes_caps_strings():
    blob = b"\x00".join(b"string%02d" % i for i in range(40))
    assert len(summarize_bytes(blob)["strings"]) == 15


# --- search_files -----------------------------------------------------------------------

PHRASE = "Olá, ação"
U8 = PHRASE.encode("utf-8")
U16 = PHRASE.encode("utf-16le")


def _place(blob: bytearray, offset: int, payload: bytes) -> None:
    blob[offset : offset + len(payload)] = payload


def test_search_files_matches_across_chunk_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(text, "_CHUNK_SIZE", 1024)
    blob = bytearray(b"\xaa" * 5000)
    _place(blob, 0, U8)
    _place(blob, 1024 - 5, U8)  # atravessa o fim do 1º bloco
    _place(blob, 2048 - 7, U16)  # atravessa o fim do 2º bloco, em offset ímpar
    _place(blob, 3072 - 1, U8)  # só o 1º byte no bloco anterior
    _place(blob, 5000 - len(U16), U16)  # fim do arquivo
    path = tmp_path / "loc.bin"
    path.write_bytes(bytes(blob))

    results = search_files(str(path), PHRASE)
    assert [(r["offset"], r["encoding"]) for r in results] == [
        (0, "utf-8"), (1019, "utf-8"), (2041, "utf-16le"), (3071, "utf-8"), (5000 - len(U16), "utf-16le"),
    ]
    assert all(r["path"] == str(path) for r in results)
    hit16 = results[2]
    assert PHRASE in hit16["context_text"]
    assert hit16["context_hex"] == bytes(blob[2041 - 32 : 2041 + len(U16) + 32]).hex(" ")
    assert results[0]["context_text"].startswith(PHRASE)  # sem contexto antes do offset 0


def test_search_files_default_chunk_boundary(tmp_path):
    chunk = text._CHUNK_SIZE
    path = tmp_path / "big.pak"
    with open(path, "wb") as f:
        f.write(b"\x00" * (chunk - 4) + U16 + b"\x00" * 100)
    (hit,) = search_files(str(tmp_path), PHRASE)
    assert (hit["offset"], hit["encoding"]) == (chunk - 4, "utf-16le")
    assert PHRASE in hit["context_text"]


def test_search_files_tree_limits_and_skips(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "sub.locres").write_bytes(b"xx" + "Fala do Sora".encode("utf-16le") + b"yy")
    (tmp_path / "b.txt").write_bytes(b"... Fala do Sora ...")
    (tmp_path / "big.bin").write_bytes(b"Fala do Sora" + b"\0" * 2000)  # maior que o limite

    results = search_files(str(tmp_path), "Fala do Sora", max_file_bytes=1000)
    assert [(os.path.relpath(r["path"], tmp_path), r["offset"], r["encoding"]) for r in results] == [
        ("b.txt", 4, "utf-8"),
        (os.path.join("a", "sub.locres"), 2, "utf-16le"),
    ]
    assert results[0]["context_text"] == "... Fala do Sora ..."
    assert "Fala do Sora" in results[1]["context_text"]

    assert len(search_files(str(tmp_path), "Fala do Sora", max_results=1)) == 1
    assert len(search_files(str(tmp_path), "Fala do Sora", encodings=["utf8"])) == 2  # inclui big.bin
    assert search_files(str(tmp_path / "b.txt"), "Fala", encodings="utf-16le") == []
    with pytest.raises(FileNotFoundError, match="não encontrado"):
        search_files(str(tmp_path / "nada"), "x")
    with pytest.raises(ValueError):
        search_files(str(tmp_path), "")


# --- compare_bytes ----------------------------------------------------------------------


def _file(tmp_path, data: bytes) -> str:
    path = tmp_path / "arquivo.bin"
    path.write_bytes(data)
    return str(path)


def _hints(result) -> str:
    return "\n".join(result["hints"])


def test_compare_identical(tmp_path):
    payload = "Legenda: Olá, ação".encode("utf-16le")
    path = _file(tmp_path, b"\x00" * 100 + payload + b"\x00" * 100)
    r = compare_bytes(payload, path, 100)
    assert r["identical"] and r["diff_count"] == 0 and r["first_diff_offset"] is None and r["diffs"] == []
    assert (r["length"], r["file_bytes_read"]) == (len(payload), len(payload))
    assert r["memory_hex"] == r["file_hex"] == payload[:64].hex(" ")
    assert "Idênticos" in _hints(r)


def test_compare_constant_xor(tmp_path):
    plain = b"Hello, Sora! Where is Kairi?"
    path = _file(tmp_path, b"HDR!" + bytes(b ^ 0x5A for b in plain))
    r = compare_bytes(plain, path, 4, max_diffs=5)
    assert not r["identical"] and r["diff_count"] == len(plain) and r["first_diff_offset"] == 0
    assert len(r["diffs"]) == 5
    assert r["diffs"][0] == {"offset": 0, "memory": "48", "file": f"{0x48 ^ 0x5A:02x}"}
    assert "XOR constante" in _hints(r) and "0x5A" in _hints(r)


def test_compare_xor_preserving_zeros(tmp_path):
    plain = b"Sora\x00Riku\x00Kairi\x00"
    path = _file(tmp_path, bytes(b ^ 0x21 if b else 0 for b in plain))
    hints = _hints(compare_bytes(plain, path, 0))
    assert "XOR constante" in hints and "0x21" in hints and "bytes iguais" in hints


def test_compare_repeating_xor_key(tmp_path):
    plain = b"Kingdom Hearts III subtitles!!!!"
    key = b"\x12\x34\x56\x78"
    path = _file(tmp_path, bytes(b ^ key[i % 4] for i, b in enumerate(plain)))
    assert "chave repetida de 4 bytes: 12 34 56 78" in _hints(compare_bytes(plain, path, 0))


def test_compare_additive_shift(tmp_path):
    plain = b"Hello, Sora! Where is Kairi?"
    path = _file(tmp_path, plain)
    hints = _hints(compare_bytes(bytes((b + 3) & 0xFF for b in plain), path, 0))
    assert "Soma constante" in hints and "0x03" in hints


def test_compare_utf16_memory_vs_utf8_file_same_offset(tmp_path):
    path = _file(tmp_path, b"\x10" * 8 + U8 + b"\x00" + b"\x10" * 64)
    hints = _hints(compare_bytes(U16 + b"\x00\x00", path, 8))
    assert "a memória tem UTF-16LE e o arquivo tem UTF-8 neste offset" in hints


def test_compare_utf8_memory_vs_utf16_file_same_offset(tmp_path):
    path = _file(tmp_path, U16 + b"\x00\x00" + b"\x10" * 64)
    hints = _hints(compare_bytes(U8, path, 0))
    assert "a memória tem UTF-8 e o arquivo tem UTF-16LE neste offset" in hints


def test_compare_utf16_memory_found_as_utf8_elsewhere(tmp_path):
    phrase = "Coração valente"
    path = _file(tmp_path, b"\x01" * 300 + phrase.encode("utf-8") + b"\x00" + b"\x01" * 300)
    hints = _hints(compare_bytes(phrase.encode("utf-16le"), path, 100))
    assert "em UTF-8: encontrado no arquivo com deslocamento +200" in hints


def test_compare_found_at_different_offset(tmp_path):
    payload = b"KH3 subtitle block \x01\x02\x03"
    path = _file(tmp_path, b"\xee" * 1000 + payload + b"\xee" * 1000)
    assert "encontrado no arquivo com deslocamento +10 (offset 0x3E8)" in _hints(compare_bytes(payload, path, 990))
    assert "encontrado no arquivo com deslocamento -16" in _hints(compare_bytes(payload, path, 1016))


def test_compare_prefix_found_elsewhere(tmp_path):
    head = bytes(range(32, 64))
    path = _file(tmp_path, b"\x00" * 500 + head + b"\x00" * 500)
    memory = head + b"campos que mudaram em memoria"
    assert "primeiros 32 bytes da memória: encontrado no arquivo com deslocamento +400" in _hints(
        compare_bytes(memory, path, 100)
    )


def test_compare_byte_swapped_16_and_32(tmp_path):
    be16 = _file(tmp_path, PHRASE.encode("utf-16be"))
    assert "blocos de 16 bits" in _hints(compare_bytes(U16, be16, 0))

    values = [0x11223344, 0x55667788, 0x99AABBCC, 0xDDEEFF01]
    be32 = _file(tmp_path, struct.pack(">4I", *values))
    hints = _hints(compare_bytes(struct.pack("<4I", *values), be32, 0))
    assert "blocos de 32 bits" in hints and "16 bits" not in hints


def test_compare_partial_prefix_eof_and_zeros(tmp_path):
    data = b"Texto igual no comeco, mas depois muda"
    path = _file(tmp_path, data)
    r = compare_bytes(data[:22] + b"X" + data[23:], path, 0)
    assert r["diff_count"] == 1 and r["first_diff_offset"] == 22
    assert "Os primeiros 22 bytes coincidem" in _hints(r)

    tail = compare_bytes(data[-10:] + b"0123456789", path, len(data) - 10)
    assert tail["file_bytes_read"] == 10 and not tail["identical"]
    assert "só 10 de 20 bytes" in _hints(tail)

    zeros = compare_bytes(bytes(16), path, 0)
    assert "zerada" in _hints(zeros)


def test_compare_no_pattern_and_errors(tmp_path):
    rng = random.Random(1234)
    path = _file(tmp_path, rng.randbytes(4096))
    r = compare_bytes(rng.randbytes(64), path, 1000)
    assert "Nenhuma transformação simples" in _hints(r)
    with pytest.raises(FileNotFoundError, match="não encontrado"):
        compare_bytes(b"abc", str(tmp_path / "nada.bin"), 0)
    with pytest.raises(ValueError):
        compare_bytes(b"", path, 0)
    with pytest.raises(ValueError):
        compare_bytes(b"abc", path, -1)
