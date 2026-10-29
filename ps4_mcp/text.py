"""Ferramentas de texto e bytes para investigar memória e arquivos do PS4.

Usadas pelo servidor MCP para: hexdump, decodificação com códigos de controle visíveis,
resumo compacto de leituras grandes, busca de frases em arquivos extraídos no PC e
comparação memória × arquivo com dicas da transformação aplicada (piloto de tradução de
legendas, seção 11 do plano).
"""

from __future__ import annotations

import codecs
import math
import os
import re
import stat
import struct
from collections import Counter

__all__ = [
    "hexdump",
    "normalize_encoding",
    "decode_text",
    "encode_needle",
    "find_all",
    "extract_strings",
    "summarize_bytes",
    "search_files",
    "compare_bytes",
]

_ENCODING_ALIASES = {
    "utf8": "utf-8",
    "utf16": "utf-16le",
    "utf16le": "utf-16le",
    "utf16be": "utf-16be",
    "sjis": "shift_jis",
    "shiftjis": "shift_jis",
    "cp932": "shift_jis",
    "latin1": "latin-1",
    "iso88591": "latin-1",
    "ascii": "ascii",
}

_CHUNK_SIZE = 4 * 1024 * 1024  # leitura em blocos no search_files (arquivos de vários GB)
_NEAR = 64 * 1024  # janela de busca ao redor do offset no compare_bytes
_MAX_STRING_CHARS = 256  # corta strings enormes no extract_strings (economia de tokens)


def _hexmark(exc: UnicodeError) -> tuple[str, int]:
    if not isinstance(exc, UnicodeDecodeError):
        raise exc
    bad = exc.object[exc.start : exc.end]
    return "".join(f"<?{b:02X}>" for b in bad), exc.end


codecs.register_error("ps4_mcp.hexmark", _hexmark)


def hexdump(data: bytes, base: int = 0, width: int = 16) -> str:
    """Hexdump clássico: endereço (16 dígitos hex), bytes em hex e ASCII ('.' = não imprimível)."""
    width = max(1, int(width))
    pad = width * 3 - 1
    lines = []
    for i in range(0, len(data), width):
        chunk = bytes(data[i : i + width])
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{base + i:016x}  {chunk.hex(' '):<{pad}}  {ascii_part}")
    return "\n".join(lines)


def normalize_encoding(name: str) -> str:
    """Normaliza o nome da codificação, aceitando apelidos (utf8, utf16, sjis, cp932, latin1...).

    UTF-16 sem ordem de bytes explícita vira utf-16le (padrão do PS4/x86-64).
    """
    key = re.sub(r"[\s_-]", "", str(name).lower())
    try:
        return _ENCODING_ALIASES[key]
    except KeyError:
        raise ValueError(
            f"Codificação desconhecida: {name!r}. Use uma destas: utf-8, utf-16le, utf-16be, "
            "shift_jis, latin-1, ascii (apelidos aceitos: utf8, utf16, utf16le, utf16be, sjis, "
            "shift-jis, cp932, latin1, iso-8859-1)."
        ) from None


def _nul_offset(data: bytes, wide: bool) -> int | None:
    if not wide:
        i = data.find(b"\0")
        return i if i >= 0 else None
    i = data.find(b"\0\0")
    while i >= 0 and i % 2:  # em UTF-16 só vale a unidade 0x0000 alinhada (offset par)
        i = data.find(b"\0\0", i + 1)
    return i if i >= 0 else None


def _visible(text: str, wide: bool) -> str:
    if text.isprintable():
        return text
    fmt = "<{:04X}>" if wide else "<{:02X}>"
    return "".join(ch if ch.isprintable() else fmt.format(ord(ch)) for ch in text)


def decode_text(data: bytes, encoding: str, stop_at_nul: bool = False) -> str:
    """Decodifica bytes como texto deixando visíveis os códigos de controle.

    Para quem faz engenharia reversa de legendas enxergar o formato real:
    - caracteres de controle (C0, inclusive \\n e \\t, DEL, C1) e outros invisíveis
      (formatação, separadores, uso privado) viram tokens com o código do caractere:
      `<0A>`; em UTF-16 com 4 dígitos: `<000A>`;
    - bytes que não decodificam viram `<?XX>` (um token por byte);
    - stop_at_nul para no primeiro NUL (em UTF-16, na primeira unidade 0x0000 em offset par).
    Obs.: um '<' literal do texto não é escapado.
    """
    enc = normalize_encoding(encoding)
    wide = enc.startswith("utf-16")
    data = bytes(data)
    if stop_at_nul:
        end = _nul_offset(data, wide)
        if end is not None:
            data = data[:end]
    return _visible(data.decode(enc, errors="ps4_mcp.hexmark"), wide)


def encode_needle(text: str, encoding: str) -> bytes:
    """Codifica o texto de busca (UTF-16 sem BOM). ValueError se o texto não cabe na codificação."""
    enc = normalize_encoding(encoding)
    try:
        return text.encode(enc)
    except UnicodeEncodeError as e:
        raise ValueError(
            f"O texto não pode ser representado em {enc}: {e.object[e.start:e.end]!r} "
            f"na posição {e.start}."
        ) from None


def find_all(haystack: bytes, needle: bytes, limit: int = 100) -> list[int]:
    """Todos os offsets de `needle` em `haystack` (ocorrências sobrepostas contam), até `limit`."""
    out: list[int] = []
    if not needle:
        return out
    i = haystack.find(needle)
    while i >= 0 and len(out) < limit:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_STRING_CHARS else text[:_MAX_STRING_CHARS] + "…"


def extract_strings(data: bytes, base: int = 0, min_len: int = 5, max_count: int = 30) -> list[dict]:
    """Strings legíveis (como o `strings`): ASCII e UTF-16LE (ASCII + letras Latin-1).

    Retorna `{"address", "encoding": "ascii"|"utf-16le", "text"}` ordenado por endereço,
    no máximo `max_count` itens; textos com mais de 256 caracteres são cortados com '…'.
    """
    data = bytes(data)
    n = max(1, int(min_len))
    found = [(m.start(), "ascii", m.group().decode("ascii"))
             for m in re.finditer(rb"[\x20-\x7e]{%d,}" % n, data)]
    found += [(m.start(), "utf-16le", m.group().decode("utf-16le"))
              for m in re.finditer(rb"(?:[\x20-\x7e\xa0-\xff]\x00){%d,}" % n, data)]
    found.sort(key=lambda item: item[0])
    return [{"address": base + off, "encoding": enc, "text": _clip(text)}
            for off, enc, text in found[: max(0, max_count)]]


def summarize_bytes(data: bytes, base: int = 0) -> dict:
    """Resumo compacto de uma leitura grande (economiza tokens em vez de um hexdump inteiro).

    entropy: Shannon em bits/byte (~0 = repetitivo, ~8 = comprimido/cifrado);
    pointer_like: u64 little-endian alinhados em 8 (pelo endereço) dentro da faixa de
    ponteiros de usuário [0x10000, 0x7FFFFFFFFFFF] — muitos indicam estruturas/vtables.
    """
    data = bytes(data)
    n = len(data)
    counts = Counter(data)
    entropy = sum(c / n * math.log2(n / c) for c in counts.values()) if n else 0.0
    printable = sum(c for b, c in counts.items() if 0x20 <= b < 0x7F or b in (9, 10, 13))
    start = (-base) % 8
    aligned = data[start : start + max(0, n - start) // 8 * 8]
    pointers = sum(1 for (v,) in struct.iter_unpack("<Q", aligned) if 0x1_0000 <= v <= 0x7FFF_FFFF_FFFF)
    return {
        "size": n,
        "entropy": round(entropy, 2),
        "zero_ratio": round(counts.get(0, 0) / n, 3) if n else 0.0,
        "printable_ratio": round(printable / n, 3) if n else 0.0,
        "pointer_like": pointers,
        "strings": extract_strings(data, base, max_count=15),
        "head_hexdump": hexdump(data[:256], base),
    }


# --- busca em arquivos ------------------------------------------------------------------


def _iter_files(root: str):
    if not os.path.isdir(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            yield os.path.join(dirpath, name)


def _context_hit(f, path: str, off: int, enc: str, needle: bytes, context: int) -> dict:
    wide = enc.startswith("utf-16")
    before, after = min(context, off), context
    if wide:  # mantém a janela alinhada com a frase para o UTF-16 decodificar certo
        before -= before % 2
        after -= after % 2
    f.seek(off - before)
    window = f.read(before + len(needle) + after)
    return {
        "path": path,
        "offset": off,
        "encoding": enc,
        "context_text": decode_text(window, enc),
        "context_hex": window.hex(" "),
    }


def _search_file(path: str, needles: list[tuple[str, bytes]], quota: int, context: int) -> list[dict]:
    overlap = max(len(needle) for _, needle in needles) - 1
    last = {enc: -1 for enc, _ in needles}
    found: list[tuple[int, str, bytes]] = []
    with open(path, "rb") as f:
        buf, pos = b"", 0  # pos = offset no arquivo de buf[0]
        while len(found) < quota:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            buf += chunk
            for enc, needle in needles:
                hits = 0
                i = buf.find(needle)
                while i >= 0 and hits < quota:
                    if pos + i > last[enc]:  # a sobreposição entre blocos pode repetir achados
                        last[enc] = pos + i
                        found.append((pos + i, enc, needle))
                        hits += 1
                    i = buf.find(needle, i + 1)
            keep = min(overlap, len(buf))
            pos += len(buf) - keep
            buf = buf[len(buf) - keep :]
        found.sort()
        return [_context_hit(f, path, off, enc, needle, context) for off, enc, needle in found[:quota]]


def search_files(
    root: str,
    text: str,
    encodings: list[str] | None = None,
    max_results: int = 50,
    max_file_bytes: int = 512 * 1024 * 1024,
    context: int = 32,
) -> list[dict]:
    """Procura uma frase nos arquivos sob `root` (pasta, recursivo, ou um único arquivo).

    A frase é codificada em cada codificação (padrão utf-8 e utf-16le) e buscada byte a byte.
    Arquivos grandes são lidos em blocos com sobreposição (não carrega o arquivo inteiro);
    arquivos maiores que `max_file_bytes` e arquivos não regulares são pulados.
    Resultado: `{"path", "offset", "encoding", "context_text", "context_hex"}` com `context`
    bytes antes e depois da frase.
    """
    if not text:
        raise ValueError("Texto de busca vazio.")
    if isinstance(encodings, str):
        encodings = [encodings]
    encs: list[str] = []
    for name in encodings or ["utf-8", "utf-16le"]:
        enc = normalize_encoding(name)
        if enc not in encs:
            encs.append(enc)
    needles = [(enc, encode_needle(text, enc)) for enc in encs]
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.exists(root):
        raise FileNotFoundError(f"Caminho não encontrado no PC: {root}")
    context = max(0, int(context))
    results: list[dict] = []
    for path in _iter_files(root):
        if len(results) >= max_results:
            break
        try:
            st = os.stat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_size > max_file_bytes:
                continue
            results += _search_file(path, needles, max_results - len(results), context)
        except OSError:
            continue  # sem permissão, apagado durante a busca etc.
    return results


# --- comparação memória × arquivo -------------------------------------------------------


def _text_prefix(data: bytes, enc: str) -> str:
    """Maior prefixo de `data` que decodifica como texto legível (para no NUL/lixo)."""
    if enc.startswith("utf-16"):
        data = data[: len(data) // 2 * 2]
    out = []
    for ch in data.decode(enc, errors="replace"):
        if ch in "�\0" or not (ch.isprintable() or ch in "\t\n\r"):
            break
        out.append(ch)
    return "".join(out)


def _is_phrase(text: str) -> bool:
    return len(text.strip()) >= 4 and any(ch.isalpha() for ch in text)


def _nearest(window: bytes, win_start: int, needle: bytes, offset: int) -> tuple[int, int] | None:
    """(offset absoluto mais próximo de `offset`, nº de ocorrências) de `needle` na janela."""
    hits = [win_start + i for i in find_all(window, needle, limit=256)]
    if not hits:
        return None
    return min(hits, key=lambda a: abs(a - offset)), len(hits)


def _encoding_hints(memory: bytes, file_bytes: bytes, window: bytes, win_start: int, offset: int) -> list[str]:
    hints = []
    cases = (("UTF-16LE", "UTF-8", _text_prefix(memory, "utf-16le"), "utf-8"),
             ("UTF-8", "UTF-16LE", _text_prefix(memory, "utf-8"), "utf-16le"))
    for mem_name, file_name, phrase, file_enc in cases:
        if not _is_phrase(phrase):
            continue
        encoded = phrase.encode(file_enc)
        n = min(len(encoded), len(file_bytes))
        preview = phrase[:40] + ("…" if len(phrase) > 40 else "")
        if n >= 8 and file_bytes[:n] == encoded[:n]:
            hints.append(
                f"Mesma frase em codificações diferentes: a memória tem {mem_name} e o arquivo "
                f"tem {file_name} neste offset ({preview!r}). O jogo converte o texto ao carregar."
            )
            continue
        near = _nearest(window, win_start, encoded, offset)
        if near:
            where, count = near
            hints.append(
                f"A frase da memória ({mem_name}, {preview!r}) está no arquivo em {file_name}: "
                f"encontrado no arquivo com deslocamento {where - offset:+d} (offset 0x{where:X}"
                + (f"; {count} ocorrências em ±64 KiB" if count > 1 else "") + ")."
            )
    return hints


def _transform_hints(memory: bytes, file_bytes: bytes, diff: list[int]) -> list[str]:
    hints = []
    n = len(file_bytes)
    xor_key, xor_count = Counter(memory[i] ^ file_bytes[i] for i in diff).most_common(1)[0]
    xor_constant = len(diff) >= 3 and xor_count == len(diff)
    if xor_constant:
        same = n - len(diff)
        hints.append(
            f"XOR constante: os {len(diff)} bytes diferentes valem arquivo XOR 0x{xor_key:02X}"
            + (f" ({same} bytes iguais ficaram sem XOR, p.ex. zeros/terminadores preservados)" if same else "")
            + ". Provável ofuscação XOR simples."
        )
    else:
        for period in (2, 4, 8, 16):
            if n < 2 * period:
                break
            key = bytes(memory[i] ^ file_bytes[i] for i in range(period))
            if any(key) and all(memory[i] ^ file_bytes[i] == key[i % period] for i in range(n)):
                hints.append(f"XOR com chave repetida de {period} bytes: {key.hex(' ')} (memória = arquivo XOR chave).")
                break
        add_key, add_count = Counter((memory[i] - file_bytes[i]) & 0xFF for i in diff).most_common(1)[0]
        if len(diff) >= 3 and add_count == len(diff):
            hints.append(
                f"Soma constante: memória = arquivo + 0x{add_key:02X} (mod 256) em todos os bytes "
                "diferentes (cifra de deslocamento simples)."
            )
    for width in (2, 4, 8):
        units = {i // width for i in diff if (i // width + 1) * width <= n}
        if len(units) < 2:
            continue
        fixed = sum(1 for u in units
                    if memory[u * width : (u + 1) * width] == file_bytes[u * width : (u + 1) * width][::-1])
        if fixed >= 2 and fixed >= 0.9 * len(units):
            hints.append(
                f"Bytes trocados em blocos de {width * 8} bits: {fixed} de {len(units)} blocos diferentes "
                "batem com a ordem invertida (endianness diferente"
                + (", ex.: UTF-16BE × UTF-16LE" if width == 2 else "") + ")."
            )
            break
    return hints


def compare_bytes(memory: bytes, file_path: str, offset: int, max_diffs: int = 32) -> dict:
    """Compara bytes lidos da memória com o arquivo no `offset` (mesmo tamanho).

    Os offsets em `diffs`/`first_diff_offset` são relativos ao início da comparação
    (no arquivo: offset + N). `hints` (pt-BR) tenta explicar a transformação: XOR constante,
    soma constante, endianness (16/32/64 bits), UTF-16LE × UTF-8 da mesma frase e os mesmos
    bytes achados em outro offset (até ±64 KiB).
    """
    memory = bytes(memory)
    if not memory:
        raise ValueError("Nada para comparar: os bytes da memória estão vazios.")
    if offset < 0:
        raise ValueError(f"Offset negativo: {offset}.")
    try:
        with open(file_path, "rb") as f:
            f.seek(offset)
            file_bytes = f.read(len(memory))
            win_start = max(0, offset - _NEAR)
            f.seek(win_start)
            window = f.read(offset - win_start + len(memory) + _NEAR)
    except FileNotFoundError:
        raise FileNotFoundError(f"Arquivo não encontrado no PC: {file_path}") from None
    except OSError as e:
        raise OSError(f"Não foi possível ler {file_path}: {e.strerror or e}") from None

    n = len(file_bytes)
    diff = [i for i in range(n) if memory[i] != file_bytes[i]]
    hints = []
    if n < len(memory):
        hints.append(f"O arquivo acaba antes: só {n} de {len(memory)} bytes existem a partir do offset 0x{offset:X}.")
    if n and not diff:
        hints.append(
            "Idênticos: a memória contém exatamente estes bytes do arquivo (carregado sem transformação)."
            if n == len(memory) else f"Os {n} bytes disponíveis no arquivo são idênticos aos da memória."
        )
    else:
        if not any(memory):
            hints.append("A memória lida está toda zerada (região ainda não preenchida, já liberada ou endereço errado?).")
        if diff and diff[0] >= 8:
            hints.append(f"Os primeiros {diff[0]} bytes coincidem; a diferença começa em +{diff[0]} (0x{diff[0]:X}).")
        if diff:
            hints += _transform_hints(memory, file_bytes, diff)
        # dados pouco variados (zeros, 'AAAA') aparecem em todo lugar: não vale procurar
        near = _nearest(window, win_start, memory, offset) if len(set(memory)) >= 3 else None
        if near and near[0] != offset:
            where, count = near
            hints.append(
                f"Mesmos bytes da memória em outro ponto: encontrado no arquivo com deslocamento {where - offset:+d} "
                f"(offset 0x{where:X}); o offset informado parece errado"
                + (f" ({count} ocorrências em ±64 KiB)" if count > 1 else "") + "."
            )
        elif not near and len(memory) > 32 and len(set(memory[:32])) >= 4:
            near = _nearest(window, win_start, memory[:32], offset)
            if near and near[0] != offset:
                hints.append(
                    f"Os primeiros 32 bytes da memória: encontrado no arquivo com deslocamento "
                    f"{near[0] - offset:+d} (offset 0x{near[0]:X})."
                )
        hints += _encoding_hints(memory, file_bytes, window, win_start, offset)
        if not hints:
            hints.append(
                "Nenhuma transformação simples detectada (XOR, soma, endianness, UTF-8↔UTF-16, "
                "deslocamento de até ±64 KiB). Pode ser outro trecho ou arquivo, dado comprimido ou "
                "cifrado, ou uma tabela de caracteres própria do jogo."
            )
    return {
        "length": len(memory),
        "file_bytes_read": n,
        "identical": n == len(memory) and not diff,
        "diff_count": len(diff),
        "first_diff_offset": diff[0] if diff else None,
        "diffs": [{"offset": i, "memory": f"{memory[i]:02x}", "file": f"{file_bytes[i]:02x}"}
                  for i in diff[: max(0, max_diffs)]],
        "memory_hex": memory[:64].hex(" "),
        "file_hex": file_bytes[:64].hex(" "),
        "hints": hints,
    }
