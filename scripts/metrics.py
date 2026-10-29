#!/usr/bin/env python3
"""Métricas da fase 5 a partir de logs/audit.jsonl (o ps4-mcp registra toda chamada).

Mostra: chamadas por ferramenta, erros, marcos (mark_milestone) e o tempo até a causa.
Uso: uv run python scripts/metrics.py [--log logs/audit.jsonl] [--start inicio] [--end causa_encontrada]
Cole a saída (Markdown) em docs/relatorio-fase5.md. O custo em tokens vem do /cost do Claude Code.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def fmt_s(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, default=Path("logs/audit.jsonl"))
    ap.add_argument("--start", default="inicio", help="marco que inicia a contagem (padrão: inicio; senão a 1ª chamada)")
    ap.add_argument("--end", default="causa_encontrada", help="marco que encerra a contagem")
    args = ap.parse_args()
    if not args.log.exists():
        print(f"{args.log} não existe: rode uma investigação com o ps4-mcp primeiro")
        return 1
    entries = [json.loads(line) for line in args.log.read_text(encoding="utf-8").splitlines() if line.strip()]
    milestones = [e for e in entries if e["kind"] == "milestone"]
    start = next((e["ts"] for e in milestones if e["name"] == args.start), None)
    start = start or next((e["ts"] for e in entries if e["kind"] == "call"), None)
    end = next((e["ts"] for e in milestones if e["name"] == args.end and (start is None or e["ts"] >= start)), None)
    window = [e for e in entries if start is None or (e["ts"] >= start and (end is None or e["ts"] <= end))]
    calls = Counter(e["name"] for e in window if e["kind"] == "call")
    errors = Counter(e["name"] for e in window if e["kind"] == "error")
    events = Counter(e["name"] for e in window if e["kind"] == "event")

    print("## Métricas da investigação\n")
    print("| Métrica | Valor |\n|---|---|")
    if start:
        print(f"| Início | {datetime.fromtimestamp(start):%Y-%m-%d %H:%M:%S} |")
    print(f"| Tempo até `{args.end}` | {fmt_s(end - start) if start and end else 'marco não registrado'} |")
    print(f"| Chamadas de ferramenta | {sum(calls.values())} |")
    print(f"| Erros | {sum(errors.values())} |")
    print(f"| Eventos do debugger | {dict(events) or 0} |")
    print(f"| Hipóteses erradas (marco `hipotese_errada`) | {sum(1 for m in milestones if m['name'] == 'hipotese_errada' and m in window)} |")
    print("\n### Chamadas por ferramenta\n\n| Ferramenta | Chamadas | Erros |\n|---|---|---|")
    for name, count in calls.most_common():
        print(f"| {name} | {count} | {errors.get(name, 0)} |")
    if milestones:
        print("\n### Marcos\n\n| Quando | Marco | Nota |\n|---|---|---|")
        for m in milestones:
            delta = f"+{fmt_s(m['ts'] - start)}" if start and m["ts"] >= start else ""
            print(f"| {datetime.fromtimestamp(m['ts']):%H:%M:%S} {delta} | {m['name']} | {m.get('note', '')} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
