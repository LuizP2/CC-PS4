#!/usr/bin/env bash
# Teste ponta a ponta no host (chamado por "make host-test"):
#   1. sobe test_host/bridge_stub.py (HTTP em porta livre + anúncio UDP);
#   2. roda build/host/e2e_main (núcleo + net_bsd.c + platform_host.c);
#   3. confere que o stub RECEBEU as 2 mensagens com o UTF-8 exato;
#   4. converte os quadros PPM em PNG (build/host/screenshots/*.png).
# Usa portas de teste (UDP 18766 por padrão) para não colidir com um bridge real.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$HERE")"
OUT="$APP/build/host"
SHOTS="$OUT/screenshots"
UDP_PORT="${CCPS4_TEST_UDP_PORT:-18766}"
PY="${PYTHON:-python3}"

mkdir -p "$SHOTS"
rm -f "$OUT/messages.jsonl" "$OUT/stub_port" "$SHOTS"/*.ppm "$SHOTS"/*.png

"$PY" -I "$HERE/bridge_stub.py" --http-port 0 --udp-port "$UDP_PORT" --interval 0.5 \
    --unicast 127.0.0.1 --inject-malformed --messages-out "$OUT/messages.jsonl" \
    --port-file "$OUT/stub_port" > "$OUT/stub.log" 2>&1 &
STUB=$!
trap 'kill $STUB 2>/dev/null || true' EXIT

for _ in $(seq 50); do [[ -s "$OUT/stub_port" ]] && break; sleep 0.1; done
[[ -s "$OUT/stub_port" ]] || { echo "stub não subiu:"; cat "$OUT/stub.log"; exit 1; }
echo "[host-test] stub no ar (HTTP $(cat "$OUT/stub_port"), UDP $UDP_PORT)"

CCPS4_QUIET=1 "$OUT/e2e_main" --udp-port "$UDP_PORT" --out-dir "$SHOTS" --expect-malformed

wait "$STUB" 2>/dev/null || true
trap - EXIT

"$PY" -I - "$OUT/messages.jsonl" <<'EOF'
import json, sys
msgs = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
want = ["pode retomar o jogo (resume)", "teste pelo teclado: ação, coração e “aspas” ✓"]
got = [m["text"] for m in msgs]
ok = got == want and all(bytes.fromhex(m["raw_hex"]).decode("utf-8") == w for m, w in zip(msgs, want))
print(f"[host-test] stub recebeu {len(msgs)} mensagem(ns): {got}")
sys.exit(0 if ok else f"[host-test] mensagens erradas; esperado {want}")
EOF

for f in "$SHOTS"/*.ppm; do
    "$PY" -I "$HERE/ppm2png.py" "$f" "${f%.ppm}.png" >/dev/null
    rm -f "$f"
done
ls -la "$SHOTS"
echo "[host-test] OK"
