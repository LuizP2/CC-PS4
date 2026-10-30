"""Extrai arquivos de um pak UE4 v4 pelo FTP. Uso: pak_extract.py <pak> <index.json> <regex> <pasta_saida>"""
import os
from pathlib import Path
WORK = Path(__file__).resolve().parents[2] / "kh3_work"  # fora do git: chave e arquivos do jogo
import ftplib, json, re, sys, zlib
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


pak, index, pattern, out = sys.argv[1:5]
f = ftplib.FTP(); f.connect(IP, 2121, timeout=30); f.login(); f.voidcmd("TYPE I")
aes = lambda b: Cipher(algorithms.AES(KEY), modes.ECB()).decryptor().update(b)
for e in json.load(open(index)):
    if not re.search(pattern, e["name"]):
        continue
    # cabeçalho do FPakEntry repetido antes dos dados: 8+8+8+4+20 (+4+16*blocos) +1+4
    header = 53 + (4 + 16 * len(e["blocks"]) if e["method"] else 0)
    if e["method"]:  # zlib por blocos; no v4 os offsets dos blocos são absolutos no pak
        data = b""
        for start, end in e["blocks"]:
            raw = ftp_range(f, ROOT + pak, start, end - start + (-(end - start) % 16 if e["encrypted"] else 0))
            data += zlib.decompress(aes(raw)[: end - start] if e["encrypted"] else raw)
    else:
        n = e["size"] + (-e["size"] % 16 if e["encrypted"] else 0)
        raw = ftp_range(f, ROOT + pak, e["offset"] + header, n)
        data = (aes(raw) if e["encrypted"] else raw)[: e["size"]]
    dst = Path(out) / e["name"].replace("../../../", "")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    print(dst, len(data))
