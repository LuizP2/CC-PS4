"""Baixa e decifra o índice de um pak UE4 (v4) pelo FTP e lista os arquivos. Uso: pak_index.py <pak> <saida.json>"""
import os
from pathlib import Path
WORK = Path(__file__).resolve().parents[2] / "kh3_work"  # fora do git: chave e arquivos do jogo
import ftplib, json, struct, sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

IP = os.environ.get("PS4_IP", "10.42.0.145")
ROOT = "/mnt/sandbox/pfsmnt/CUSA12025-app0/tresgame/Content/Paks/"
KEY = (WORK / "kh3_aes.txt").read_text().strip().encode()


def ftp_range(f, path, off, n):
    conn, buf = f.transfercmd("RETR " + path, rest=off), bytearray()
    while len(buf) < n and (d := conn.recv(min(1 << 20, n - len(buf)))):
        buf += d
    conn.close()
    try:
        f.voidresp()
    except ftplib.error_temp:
        pass  # abortamos no meio: o servidor responde 426
    return bytes(buf)


f = ftplib.FTP(); f.connect(IP, 2121, timeout=30); f.login(); f.voidcmd("TYPE I")
path = ROOT + sys.argv[1]
size = f.size(path)
foot = ftp_range(f, path, size - 45, 45)  # flag de criptografia + magic, versão, offset, tamanho, SHA1
enc, magic, ver, off, isz = struct.unpack_from("<BIIQQ", foot)
assert magic == 0x5A6F12E1, hex(magic)
raw = ftp_range(f, path, off, isz)
data = Cipher(algorithms.AES(KEY), modes.ECB()).decryptor().update(raw[: len(raw) // 16 * 16]) if enc else raw

pos = 0
def u(fmt):
    global pos
    v = struct.unpack_from(fmt, data, pos); pos += struct.calcsize(fmt); return v
def fstr():
    (n,) = u("<i")
    global pos
    if n < 0:
        s = data[pos:pos - n * 2].decode("utf-16le"); pos += -n * 2
    else:
        s = data[pos:pos + n].decode("utf-8", "replace"); pos += n
    return s.rstrip("\x00")

mount = fstr()
(count,) = u("<i")
files = []
for _ in range(count):
    name = fstr()
    offset, csize, usize, method = u("<qqqi")
    pos += 20  # SHA1
    blocks = []
    if method:
        (nb,) = u("<i")
        blocks = [u("<qq") for _ in range(nb)]
    encrypted, block_size = u("<BI")
    files.append({"name": mount + name, "offset": offset, "size": csize, "usize": usize, "method": method,
                  "encrypted": encrypted, "blocks": blocks, "block_size": block_size})
json.dump(files, open(sys.argv[2], "w"))
print(f"{sys.argv[1]}: v{ver}, mount {mount!r}, {count} arquivos")
