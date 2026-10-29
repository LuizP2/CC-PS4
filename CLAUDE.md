# CC-PS4

Agente Claude Code com acesso ao PS4 em tempo de execução. O plano completo está em `ps4-claude-agent-plano.md` e a implementação no `README.md`.

## Servidor MCP `ps4` (este repositório)

- **Código:** `ps4_mcp/`.
- **Registro:** `.mcp.json`. Ele lê o IP do console de `PS4_IP` no ambiente.
- **Testes:** `uv run pytest`, sem PS4, contra o ps4debug falso em `tests/fakes/`.
  - O hook RTK resume a saída do pytest de forma enganosa. Grave em arquivo e leia: `uv run pytest > /tmp/x.txt 2>&1; tail /tmp/x.txt`.
- **Fluxo:** `ps4_status` → `attach` → `set_breakpoint("eboot.bin+0x…")`/`set_watchpoint` → `wait_for_event` (repita no timeout) → investigar com o processo pausado → `resume`/`step`.
- **Pseudocódigo:** use `dump_module` e importe o ELF no ReVa. Os endereços no Ghidra são os endereços de execução.
- **Falha de conexão ou de ATTACH:** rode `ps4_diagnose` primeiro (porta 755 do PC, firewall, sysctl).
- **Regras do payload:** antes de mexer em `ps4_mcp/client.py` ou `debug.py`, leia `docs/protocolo-ps4debug.md`. Erro derruba a conexão, o ATTACH falho trava o payload e comando de debug sem sessão crasha o payload.
- **Investigações:** marque marcos com `mark_milestone` (inicio, arquivo_encontrado, causa_encontrada, hipotese_errada). O `scripts/metrics.py` gera o relatório da fase 5.

## Ferramentas e skills

- **RE de binários:** MCP `ReVa` (Ghidra 12.1.3 headless em `~/tools/ghidra_12.1.3_PUBLIC`, projetos em `.reva/projects/`). Skills: `binary-triage`, `deep-analysis`, `pyghidra-scripting`, `reverse-engineering`, `re`, `helper-recovery`.
- **Port / GPU PS4:** `ps4-gnm-port` (GNM, Liverpool gfx7, shaders), `native-platform-porting`, plugin `console-skill` (`console-reverse`, `console-backend`, `console-graphics`...).
- **Homebrew:** toolchain OpenOrbis em `~/tools/OpenOrbis/PS4Toolchain` (`scripts/install_openorbis.sh`). PKGs em `tests/crash-homebrew/` (fase 3) e `ps4-app/` (fase 6).
- **Harness / MCP:** `agentic-harness-patterns`.
- **Mapa do código:** `graphify` (consultar `graphify-out/` quando existir).
- **Economia de tokens:** caveman, ponytail, context-mode e RTK ativos globalmente. Hexdumps e logs grandes passam pelo context-mode ou viram arquivo em `dumps/`.
