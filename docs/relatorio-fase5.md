# Relatório da fase 5: teste de valor

> Preencher depois de uma investigação real. As métricas saem de `uv run python scripts/metrics.py`, que lê `logs/audit.jsonl`. O custo em tokens sai do `/cost` do Claude Code ou da linha de status do mod `session-usage`.

## 1. Caso investigado

| Item | Valor |
|---|---|
| Projeto | (piloto KH3 PT-BR, ou outro port) |
| Bug / pergunta | |
| Jogo / title id / versão | (`ps4_status`) |
| Firmware / payload | (ex.: 9.00 / ps4debug 1.x / GoldHEN 2.x) |
| Data | |

## 2. Como rodar

1. Antes de começar, peça ao agente `mark_milestone("inicio", "<descrição do bug>")`.
2. A cada descoberta, registre um marco:
   - `arquivo_encontrado`
   - `formato_documentado`
   - `causa_encontrada`
   - `hipotese_errada` (com nota do que estava errado e se o agente percebeu sozinho)
3. Ao final, rode `scripts/metrics.py` e cole a saída abaixo.

## 3. Métricas

(saída do `scripts/metrics.py`)

| Métrica | Agente | Fluxo manual de costume |
|---|---|---|
| Tempo até a causa | | |
| Chamadas de ferramenta | | n/a |
| Custo aproximado em tokens | | n/a |
| Hipóteses erradas (e se o agente percebeu) | | |

## 4. O que funcionou e o que atrapalhou

-

## 5. Decisão

- [ ] Continuar o projeto
- [ ] Ajustar antes de continuar (o quê):
- [ ] Parar

Material para o fórum: anexar este relatório e um trecho anonimizado do `logs/audit.jsonl`.
