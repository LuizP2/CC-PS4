# Graph Report - .  (2026-10-29)

## Corpus Check
- Corpus is ~3,002 words - fits in a single context window. You may not need a graph.

## Summary
- 40 nodes · 49 edges · 6 communities
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 5 edges (avg confidence: 0.79)
- Token cost: 0 input · 67,767 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Debug Events & RE Link|Debug Events & RE Link]]
- [[_COMMUNITY_Toolchain & Token Setup|Toolchain & Token Setup]]
- [[_COMMUNITY_Plan Phases & KH3 Pilot|Plan Phases & KH3 Pilot]]
- [[_COMMUNITY_Memory Read Tools|Memory Read Tools]]
- [[_COMMUNITY_Agent Loop Architecture|Agent Loop Architecture]]
- [[_COMMUNITY_Safety & Write Gating|Safety & Write Gating]]

## God Nodes (most connected - your core abstractions)
1. `ps4-mcp server` - 12 edges
2. `CC-PS4 CLAUDE.md` - 6 edges
3. `ps4debug Python client (PyPI)` - 5 edges
4. `Read tools (MVP)` - 4 edges
5. `Implementation phases 0-6` - 4 edges
6. `PS4 Agent implementation plan` - 3 edges
7. `REA MCP (Ghidra/IDA)` - 3 edges
8. `read_memory tool` - 3 edges
9. `download_file tool` - 3 edges
10. `wait_for_event tool` - 3 edges

## Surprising Connections (you probably didn't know these)
- `ReVa MCP (Ghidra 12.1.3 headless)` --semantically_similar_to--> `REA MCP (Ghidra/IDA)`  [INFERRED] [semantically similar]
  CLAUDE.md → ps4-claude-agent-plano.md
- `ps4-gnm-port skill` --conceptually_related_to--> `OpenOrbis build/deploy`  [INFERRED]
  CLAUDE.md → ps4-claude-agent-plano.md
- `agentic-harness-patterns skill` --conceptually_related_to--> `ps4-mcp server`  [EXTRACTED]
  CLAUDE.md → ps4-claude-agent-plano.md
- `CC-PS4 CLAUDE.md` --references--> `PS4 Agent implementation plan`  [EXTRACTED]
  CLAUDE.md → ps4-claude-agent-plano.md
- `Token economy (caveman, ponytail, context-mode, RTK)` --rationale_for--> `Risk table`  [INFERRED]
  CLAUDE.md → ps4-claude-agent-plano.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Event-driven breakpoint investigation flow** — cc_ps4_ps4_claude_agent_plano_debug_tools, cc_ps4_ps4_claude_agent_plano_wait_for_event, cc_ps4_ps4_claude_agent_plano_event_queue, cc_ps4_ps4_claude_agent_plano_event_snapshot, cc_ps4_ps4_claude_agent_plano_module_offset, cc_ps4_ps4_claude_agent_plano_rea_mcp [EXTRACTED 1.00]
- **ps4-mcp console access backends** — cc_ps4_ps4_claude_agent_plano_ps4debug_python_client, cc_ps4_ps4_claude_agent_plano_ftp_server, cc_ps4_ps4_claude_agent_plano_log_reader, cc_ps4_ps4_claude_agent_plano_event_queue [EXTRACTED 1.00]
- **Risk-tiered tool catalog** — cc_ps4_ps4_claude_agent_plano_read_tools, cc_ps4_ps4_claude_agent_plano_debug_tools, cc_ps4_ps4_claude_agent_plano_write_tools, cc_ps4_ps4_claude_agent_plano_security [INFERRED 0.85]

## Communities (6 total, 0 thin omitted)

### Community 0 - "Debug Events & RE Link"
Cohesion: 0.22
Nodes (9): Debug tools (phase 3), download_file tool, Event queue (asyncio.Queue), Event snapshot format, Console FTP server (GoldHEN), Log reader, module_offset, REA MCP (Ghidra/IDA) (+1 more)

### Community 1 - "Toolchain & Token Setup"
Cohesion: 0.25
Nodes (8): CC-PS4 CLAUDE.md, agentic-harness-patterns skill, graphify code map, ps4-gnm-port skill, ReVa MCP (Ghidra 12.1.3 headless), Token economy (caveman, ponytail, context-mode, RTK), Pausing alters timing (race bugs vanish), Risk table

### Community 2 - "Plan Phases & KH3 Pilot"
Cohesion: 0.25
Nodes (8): PS4 Agent implementation plan, Kingdom Hearts 3 PT-BR subtitle pilot, OpenOrbis build/deploy, Phase 5 value test, Implementation phases 0-6, Pilot tools (search_memory, read_text_region, search_files, compare_bytes), PS4 interface app (phase 6, optional), UE4 .pak localization hypothesis

### Community 3 - "Memory Read Tools"
Cohesion: 0.47
Nodes (5): list_processes tool, process_maps tool, ps4debug Python client (PyPI), read_memory tool, Read tools (MVP)

### Community 4 - "Agent Loop Architecture"
Cohesion: 0.40
Nodes (5): Autonomous agent debug loop, FastMCP, Agent loop runs on PC, ps4-mcp server, ps4debug / ps4debug-NG payload

### Community 5 - "Safety & Write Gating"
Cohesion: 0.50
Nodes (4): ps4-agent repository structure, safety.py, Security policy (LAN only, audit log, limits), Write tools (disabled by default)

## Knowledge Gaps
- **6 isolated node(s):** `graphify code map`, `Debug tools (phase 3)`, `Write tools (disabled by default)`, `FastMCP`, `UE4 .pak localization hypothesis` (+1 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ps4-mcp server` connect `Agent Loop Architecture` to `Debug Events & RE Link`, `Toolchain & Token Setup`, `Plan Phases & KH3 Pilot`, `Memory Read Tools`, `Safety & Write Gating`?**
  _High betweenness centrality (0.704) - this node is a cross-community bridge._
- **Why does `CC-PS4 CLAUDE.md` connect `Toolchain & Token Setup` to `Plan Phases & KH3 Pilot`?**
  _High betweenness centrality (0.276) - this node is a cross-community bridge._
- **Why does `PS4 Agent implementation plan` connect `Plan Phases & KH3 Pilot` to `Toolchain & Token Setup`, `Agent Loop Architecture`?**
  _High betweenness centrality (0.244) - this node is a cross-community bridge._
- **What connects `graphify code map`, `Autonomous agent debug loop`, `Agent loop runs on PC` to the rest of the system?**
  _9 weakly-connected nodes found - possible documentation gaps or missing edges._