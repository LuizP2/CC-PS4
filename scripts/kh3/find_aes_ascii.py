"""Testa como chave AES toda sequência de 32 caracteres ASCII imprimíveis do eboot (qualquer alinhamento)."""
import os
from pathlib import Path
WORK = Path(__file__).resolve().parents[2] / "kh3_work"  # fora do git: chave e arquivos do jogo
import ftplib, re, struct, sys, time
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from ps4_mcp.client import PS4Debug
IP = os.environ.get("PS4_IP", "10.42.0.145")
PAK = "/mnt/sandbox/pfsmnt/CUSA12025-app0/tresgame/Content/Paks/pakchunk0-ps4.pak"
f = ftplib.FTP(); f.connect(IP, 2121, timeout=20); f.login(); f.voidcmd("TYPE I")
conn = f.transfercmd("RETR " + PAK, rest=1225317212); block = b""
while len(block) < 16: block += conn.recv(16 - len(block))
conn.close()
def ok(p):
    n = struct.unpack_from("<i", p)[0]
    return (1 < n < 512 and p[4:7] == b"../") or (-512 < n < -1 and p[4:10] == "../".encode("utf-16le"))
c, t, tested = PS4Debug(IP, 744), time.time(), 0
PID = next(p for p, n in c.processes() if n == "eboot.bin")  # o jogo aberto
for s, e in ((0x87e4000, 0xb450000), (0x400000, 0x87e4000)):
    for base in range(s, e, 0x100000):
        buf = c.read(PID, base, min(0x100020, e - base))
        for m in re.finditer(rb"[\x21-\x7e]{32,}", buf):
            run = m.group(0)
            for i in range(len(run) - 31):
                tested += 1
                if ok(Cipher(algorithms.AES(run[i:i + 32]), modes.ECB()).decryptor().update(block)):
                    open(sys.argv[1], "w").write(run[i:i + 32].decode() + "\n")
                    print(f"CHAVE ASCII em {base + m.start() + i:#x}, salva em {sys.argv[1]} ({tested} testes, {time.time() - t:.0f}s)")
                    sys.exit(0)
print(f"nenhuma ({tested} testes, {time.time() - t:.0f}s)")
