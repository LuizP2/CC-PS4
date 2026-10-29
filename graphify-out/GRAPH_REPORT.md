# Graph Report - CC-PS4  (2026-10-29)

## Corpus Check
- 2 files · ~3,002 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 75 nodes · 82 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 5 edges (avg confidence: 0.79)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Debug Events & RE Link|Debug Events & RE Link]]
- [[_COMMUNITY_Toolchain & Token Setup|Toolchain & Token Setup]]
- [[_COMMUNITY_Plan Phases & KH3 Pilot|Plan Phases & KH3 Pilot]]
- [[_COMMUNITY_Memory Read Tools|Memory Read Tools]]
- [[_COMMUNITY_Agent Loop Architecture|Agent Loop Architecture]]
- [[_COMMUNITY_Safety & Write Gating|Safety & Write Gating]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]

## God Nodes (most connected - your core abstractions)
1. `PS4 Agent — Claude Code com acesso ao PS4 em tempo de execução` - 13 edges
2. `ps4-mcp server` - 12 edges
3. `7. Fases` - 8 edges
4. `11. Projeto piloto: tradução das legendas do Kingdom Hearts 3` - 7 edges
5. `CC-PS4 CLAUDE.md` - 6 edges
6. `4. Catálogo de ferramentas do `ps4-mcp`` - 5 edges
7. `ps4debug Python client (PyPI)` - 5 edges
8. `Read tools (MVP)` - 4 edges
9. `Implementation phases 0-6` - 4 edges
10. `PS4 Agent implementation plan` - 3 edges

## Surprising Connections (you probably didn't know these)
- `ReVa MCP (Ghidra 12.1.3 headless)` --semantically_similar_to--> `REA MCP (Ghidra/IDA)`  [INFERRED] [semantically similar]
  CLAUDE.md → ps4-claude-agent-plano.md
- `agentic-harness-patterns skill` --conceptually_related_to--> `ps4-mcp server`  [EXTRACTED]
  CLAUDE.md → ps4-claude-agent-plano.md
- `ps4-gnm-port skill` --conceptually_related_to--> `OpenOrbis build/deploy`  [INFERRED]
  CLAUDE.md → ps4-claude-agent-plano.md
- `Token economy (caveman, ponytail, context-mode, RTK)` --rationale_for--> `Risk table`  [INFERRED]
  CLAUDE.md → ps4-claude-agent-plano.md
- `CC-PS4 CLAUDE.md` --references--> `PS4 Agent implementation plan`  [EXTRACTED]
  CLAUDE.md → ps4-claude-agent-plano.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Event-driven breakpoint investigation flow** — cc_ps4_ps4_claude_agent_plano_debug_tools, cc_ps4_ps4_claude_agent_plano_wait_for_event, cc_ps4_ps4_claude_agent_plano_event_queue, cc_ps4_ps4_claude_agent_plano_event_snapshot, cc_ps4_ps4_claude_agent_plano_module_offset, cc_ps4_ps4_claude_agent_plano_rea_mcp [EXTRACTED 1.00]
- **ps4-mcp console access backends** — cc_ps4_ps4_claude_agent_plano_ps4debug_python_client, cc_ps4_ps4_claude_agent_plano_ftp_server, cc_ps4_ps4_claude_agent_plano_log_reader, cc_ps4_ps4_claude_agent_plano_event_queue [EXTRACTED 1.00]
- **Risk-tiered tool catalog** — cc_ps4_ps4_claude_agent_plano_read_tools, cc_ps4_ps4_claude_agent_plano_debug_tools, cc_ps4_ps4_claude_agent_plano_write_tools, cc_ps4_ps4_claude_agent_plano_security [INFERRED 0.85]

## Communities (10 total, 1 thin omitted)

### Community 0 - "Debug Events & RE Link"
Cohesion: 0.33
Nodes (6): Debug tools (phase 3), Event queue (asyncio.Queue), Event snapshot format, module_offset, REA MCP (Ghidra/IDA), wait_for_event tool

### Community 1 - "Toolchain & Token Setup"
Cohesion: 0.18
Nodes (12): CC-PS4 CLAUDE.md, agentic-harness-patterns skill, graphify code map, ps4-gnm-port skill, ReVa MCP (Ghidra 12.1.3 headless), Token economy (caveman, ponytail, context-mode, RTK), PS4 Agent implementation plan, Pausing alters timing (race bugs vanish) (+4 more)

### Community 2 - "Plan Phases & KH3 Pilot"
Cohesion: 0.50
Nodes (4): Kingdom Hearts 3 PT-BR subtitle pilot, Phase 5 value test, Pilot tools (search_memory, read_text_region, search_files, compare_bytes), UE4 .pak localization hypothesis

### Community 3 - "Memory Read Tools"
Cohesion: 0.19
Nodes (13): Autonomous agent debug loop, download_file tool, FastMCP, Console FTP server (GoldHEN), list_processes tool, Log reader, Agent loop runs on PC, process_maps tool (+5 more)

### Community 4 - "Agent Loop Architecture"
Cohesion: 0.17
Nodes (11): 10. Modelo de execução: o agente fica vivo durante o jogo, 12. Estrutura do repositório, 1. Objetivo, 2. Arquitetura, 3. Pré-requisitos, 5. Esqueleto do servidor MCP, 6. Eventos em "tempo real", 8. Segurança (+3 more)

### Community 5 - "Safety & Write Gating"
Cohesion: 0.50
Nodes (4): ps4-agent repository structure, safety.py, Security policy (LAN only, audit log, limits), Write tools (disabled by default)

### Community 6 - "Community 6"
Cohesion: 0.25
Nodes (8): 7. Fases, Fase 0 — Ambiente e primitivas (sem IA), Fase 1 — MCP de leitura, Fase 2 — Sistema de arquivos e logs, Fase 3 — Debug e eventos, Fase 4 — Integração com RE, Fase 5 — Teste de valor (decide se o projeto continua), Fase 6 — Opcional

### Community 7 - "Community 7"
Cohesion: 0.29
Nodes (7): 11.1 Objetivo, 11.2 Onde o agente ajuda (e onde não), 11.3 Ferramentas extras para este piloto, 11.4 Passo a passo do piloto, 11.5 Métricas para a Fase 5, 11.6 Riscos específicos, 11. Projeto piloto: tradução das legendas do Kingdom Hearts 3

### Community 8 - "Community 8"
Cohesion: 0.40
Nodes (5): 4.1 Leitura (MVP), 4.2 Debug (fase 3), 4.3 Escrita (desabilitadas por padrão), 4.4 Formato do snapshot de evento, 4. Catálogo de ferramentas do `ps4-mcp`

## Knowledge Gaps
- **33 isolated node(s):** `Ferramentas e skills para este projeto`, `O que este projeto NÃO reimplementa`, `2. Arquitetura`, `3. Pré-requisitos`, `4.1 Leitura (MVP)` (+28 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ps4-mcp server` connect `Memory Read Tools` to `Debug Events & RE Link`, `Toolchain & Token Setup`, `Plan Phases & KH3 Pilot`, `Safety & Write Gating`?**
  _High betweenness centrality (0.193) - this node is a cross-community bridge._
- **Why does `PS4 Agent — Claude Code com acesso ao PS4 em tempo de execução` connect `Agent Loop Architecture` to `Community 8`, `Community 6`, `Community 7`?**
  _High betweenness centrality (0.150) - this node is a cross-community bridge._
- **What connects `Ferramentas e skills para este projeto`, `O que este projeto NÃO reimplementa`, `2. Arquitetura` to the rest of the system?**
  _36 weakly-connected nodes found - possible documentation gaps or missing edges._