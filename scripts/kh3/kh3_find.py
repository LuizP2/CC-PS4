"""Procura textos na memória do KH3 pulando MiBs zerados (amostra de 4 KiB por MiB).
Uso: kh3_find.py texto1 [texto2 ...]"""
import os
from pathlib import Path
WORK = Path(__file__).resolve().parents[2] / "kh3_work"  # fora do git: chave e arquivos do jogo
import sys, time
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ps4_mcp.client import PS4Debug, PS4DebugError

IP, CH = os.environ.get("PS4_IP", "10.42.0.145"), 0x100000
words = sys.argv[1:]
pats = {(w, e): w.encode(e) for w in words for e in ("utf-16le", "utf-8")}


class Retry:
    """Cliente que reconecta e repete a chamada quando o link cai (cabo com mau contato)."""
    def __init__(self):
        self.c = PS4Debug(IP, 744)

    def __getattr__(self, name):
        def call(*a):
            for tentativa in range(30):
                try:
                    return getattr(self.c, name)(*a)
                except (OSError, PS4DebugError):
                    time.sleep(2)
                    try:
                        self.c = PS4Debug(IP, 744)
                    except (OSError, PS4DebugError):
                        pass
            raise RuntimeError(f"{name}: sem conexão depois de 30 tentativas")
        return call


c = Retry()
PID = max(p for p, n in c.processes() if n == "eboot.bin")  # o jogo aberto mais recente
t = time.time()
import json, os
LIVE = str(WORK / f"kh3_live_{PID}.json")  # as regiões com dados mudam a cada execução do jogo
live = json.load(open(LIVE)) if os.path.exists(LIVE) else []
for m in ([] if live else c.maps(PID)):
    if not m.prot & 2:
        continue
    for s in range(m.start, m.end, CH):
        if c.read(PID, s, 0x1000).count(0) < 0x1000:
            live.append((s, min(CH, m.end - s)))
json.dump(live, open(LIVE, "w"))
print(f"{len(live)} MiB com dados ({time.time() - t:.0f}s); lendo...", flush=True)
hits = []
for s, n in live:
    buf = c.read(PID, s, n)
    for (w, e), p in pats.items():
        i = buf.find(p)
        while i != -1:
            k = min(i, 96) & ~1  # recuo par: o contexto fica alinhado com o UTF-16
            ctx = buf[i - k:i + len(p) + 96] if e == "utf-16le" else b""
            hits.append((s + i, w, e, ctx))
            i = buf.find(p, i + 1)
print(f"{len(hits)} ocorrências em {time.time() - t:.0f}s")
json.dump([[a, w, e, x.hex()] for a, w, e, x in hits], open(str(WORK / "kh3_hits.json"), "w"))
for a, w, e, x in hits:
    if e == "utf-16le":
        print(hex(a), w, repr(x.decode("utf-16le", "replace")))
