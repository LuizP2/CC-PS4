# Graph Report - CC-PS4  (2026-10-29)

## Corpus Check
- 77 files · ~70,876 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1260 nodes · 3243 edges · 60 communities (59 shown, 1 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 378 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `7ba79ac5`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

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
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 51|Community 51]]

## God Nodes (most connected - your core abstractions)
1. `FakePS4Debug` - 103 edges
2. `str` - 56 edges
3. `PS4Debug` - 52 edges
4. `DebugSession` - 49 edges
5. `int` - 48 edges
6. `PS4DebugError` - 42 edges
7. `ConsoleFTP` - 38 edges
8. `int` - 35 edges
9. `LogReader` - 32 edges
10. `FakeProcess` - 31 edges

## Surprising Connections (you probably didn't know these)
- `int` --uses--> `LogReader`  [INFERRED]
  tests/test_logs.py → ps4_mcp/logs.py
- `ReVa MCP (Ghidra 12.1.3 headless)` --semantically_similar_to--> `REA MCP (Ghidra/IDA)`  [INFERRED] [semantically similar]
  CLAUDE.md → ps4-claude-agent-plano.md
- `int` --uses--> `PS4DebugError`  [INFERRED]
  scripts/fase0_smoke.py → ps4_mcp/client.py
- `int` --uses--> `PS4Debug`  [INFERRED]
  scripts/fase0_smoke.py → ps4_mcp/client.py
- `test_process_primitives()` --calls--> `unmapped_warning()`  [EXTRACTED]
  tests/test_e2e_debug.py → ps4_mcp/client.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Event-driven breakpoint investigation flow** — cc_ps4_ps4_claude_agent_plano_debug_tools, cc_ps4_ps4_claude_agent_plano_wait_for_event, cc_ps4_ps4_claude_agent_plano_event_queue, cc_ps4_ps4_claude_agent_plano_event_snapshot, cc_ps4_ps4_claude_agent_plano_module_offset, cc_ps4_ps4_claude_agent_plano_rea_mcp [EXTRACTED 1.00]
- **ps4-mcp console access backends** — cc_ps4_ps4_claude_agent_plano_ps4debug_python_client, cc_ps4_ps4_claude_agent_plano_ftp_server, cc_ps4_ps4_claude_agent_plano_log_reader, cc_ps4_ps4_claude_agent_plano_event_queue [EXTRACTED 1.00]
- **Risk-tiered tool catalog** — cc_ps4_ps4_claude_agent_plano_read_tools, cc_ps4_ps4_claude_agent_plano_debug_tools, cc_ps4_ps4_claude_agent_plano_write_tools, cc_ps4_ps4_claude_agent_plano_security [INFERRED 0.85]

## Communities (60 total, 1 thin omitted)

### Community 0 - "Debug Events & RE Link"
Cohesion: 0.06
Nodes (58): IntEnum, OSError, Connection, PS4Debug, PS4DebugError, bool, bytes, float (+50 more)

### Community 1 - "Toolchain & Token Setup"
Cohesion: 0.07
Nodes (73): bridge_state_t, bridge_check_message(), bridge_config_defaults(), bridge_kind_name(), bridge_kind_parse(), bridge_parse_config(), bridge_parse_discovery(), bridge_parse_feed() (+65 more)

### Community 2 - "Plan Phases & KH3 Pilot"
Cohesion: 0.07
Nodes (77): _clip(), compare_bytes(), _context_hit(), decode_text(), encode_needle(), _encoding_hints(), extract_strings(), find_all() (+69 more)

### Community 3 - "Memory Read Tools"
Cohesion: 0.06
Nodes (39): CC-PS4 CLAUDE.md, agentic-harness-patterns skill, graphify code map, ps4-gnm-port skill, ReVa MCP (Ghidra 12.1.3 headless), Token economy (caveman, ponytail, context-mode, RTK), PS4 Agent implementation plan, Autonomous agent debug loop (+31 more)

### Community 4 - "Agent Loop Architecture"
Cohesion: 0.06
Nodes (31): 10. Modelo de execução: o agente fica vivo durante o jogo, 11.1 Objetivo, 11.2 Onde o agente ajuda (e onde não), 11.3 Ferramentas extras para este piloto, 11.4 Passo a passo do piloto, 11.5 Métricas para a Fase 5, 11.6 Riscos específicos, 11. Projeto piloto: tradução das legendas do Kingdom Hearts 3 (+23 more)

### Community 5 - "Safety & Write Gating"
Cohesion: 0.11
Nodes (53): build_demo(), Populate ``fake`` with system processes and a game. Returns useful addresses., attach(), dbg_listener(), fake(), _fixed_interrupt_model(), free_port(), nothing_pending() (+45 more)

### Community 6 - "Community 6"
Cohesion: 0.11
Nodes (11): FakePS4Debug, _Hang, sys_proc_vm_map_handle (kdebugger hooks.c:93-127)., proc_scan_handle (proc.c:633-719), bugs included:          * the page buffer is, Unpack a packet struct; short packets are zero padded (real: reads past the buff, The real handler thread spins forever. The connection is never served again., Fake ps4debug payload. ``start()`` binds; ``self.port`` holds the real TCP port., The payload thread faults -> SceShellCore (which hosts it) dies -> every socket (+3 more)

### Community 7 - "Community 7"
Cohesion: 0.10
Nodes (13): parse_list_line(), Interpreta uma linha de LIST estilo Unix (formato do ftps4/GoldHEN, pyftpdlib, l, client(), FakeFtps4, ftps4(), int, PyFtpd, test_connection_refused_message() (+5 more)

### Community 8 - "Community 8"
Cohesion: 0.07
Nodes (27): BaseModel, object, _keepalive(), LogReader, _matcher(), _parse_source(), BaseException, bool (+19 more)

### Community 9 - "Community 9"
Cohesion: 0.40
Nodes (4): CC-PS4, Ferramentas e skills, Ferramentas e skills para este projeto, Servidor MCP `ps4` (este repositório)

### Community 10 - "Community 10"
Cohesion: 0.08
Nodes (38): ClientSession, OO_PS4_TOOLCHAIN, PS4_BRIDGE, PS4_ENABLE_DEPLOY, PS4_ENABLE_WRITE, PS4_IP, PS4_LOG_SOURCE, PS4_WORKDIR (+30 more)

### Community 11 - "Community 11"
Cohesion: 0.09
Nodes (18): _check_regs(), FakeProcess, FakeThread, _Mapping, proc_allocate (kdebugger proc.c:137-172): vm_map_findspace = first fit from min_, proc_create_thread (kdebugger proc.c:211-356): needs an r-x libkernel*.sprx mapp, proc_load_elf (kdebugger proc.c:358-465, elf.c): uses p_paddr, only R_X86_64_REL, ptrace target resolution [FreeBSD]: ids > PID_MAX are lwpids (tdfind); otherwise (+10 more)

### Community 12 - "Community 12"
Cohesion: 0.08
Nodes (25): Exception, _ConnLost, _Crash, default_fpregs(), dr7_access(), dr7_enabled(), dr7_len(), dr7_mask() (+17 more)

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (26): MapEntry, describe_address(), find_map(), module_base(), parse_address(), parse_int(), Cliente das primitivas do ps4debug: processos, memória, kernel e notificações., Aceita 0x412abc, 4271804, "eboot.bin+0x12abc" ou "eboot.bin" (base do módulo). (+18 more)

### Community 14 - "Community 14"
Cohesion: 0.10
Nodes (15): _DebugContext, insn_length(), server.c:62-173. Returns True when the client must be freed (ECONNRESET on dbgfd, net_send_data on dbgfd. [FreeBSD] after the peer closed, the first write still, Raw memory (ignores protections); unmapped bytes read as zero., struct debug_context (protocol.h:316-324). Lives inside a client slot., struct server_client (protocol.h:326-332): one of SERVER_MAXCLIENTS static slots, net_recv_data(fd, &packet, 12, 0) on a SO_NBIO socket: reads what is there, mayb (+7 more)

### Community 15 - "Community 15"
Cohesion: 0.13
Nodes (22): build_elf(), bytes, int, Gera um ELF64 a partir de regiões de memória dumpadas, para abrir no Ghidra/ReVa, segments: (endereço, bytes, prot com bits 1=R 2=W 4=X do mapa do ps4debug)., build_snapshot(), disasm(), guess_fault() (+14 more)

### Community 16 - "Community 16"
Cohesion: 0.23
Nodes (25): apply_feed(), fail_request(), feed_cb(), feed_clear(), feed_push(), go_offline(), handle_ime(), handle_response() (+17 more)

### Community 17 - "Community 17"
Cohesion: 0.12
Nodes (10): canvas_t, net_init(), host_ime_script(), host_write_ppm(), expect(), main(), press(), run_until() (+2 more)

### Community 18 - "Community 18"
Cohesion: 0.12
Nodes (13): Audit, check_lan(), bool, int, Path, str, Segurança e auditoria (seção 8 do plano): só rede local, limites e log de toda c, Uma linha JSON por chamada de ferramenta em logs/audit.jsonl (lido por scripts/m (+5 more)

### Community 19 - "Community 19"
Cohesion: 0.12
Nodes (25): FTP, _clean_path(), ConsoleFTP, _explain(), FTPError, BaseException, bool, bytes (+17 more)

### Community 20 - "Community 20"
Cohesion: 0.10
Nodes (23): PS4 Agent: servidor MCP para o Claude Code investigar o PS4 em tempo de execução, build_homebrew(), clear_breakpoint(), clear_watchpoint(), get_registers(), _j(), list_threads(), main() (+15 more)

### Community 21 - "Community 21"
Cohesion: 0.14
Nodes (21): prot_str(), clamp(), compare_bytes(), disassemble(), dump_module(), process_maps(), int, Regiões de memória do processo: início-fim, tamanho, proteção (rwx) e nome do mó (+13 more)

### Community 22 - "Community 22"
Cohesion: 0.07
Nodes (33): Bridge, _clean(), bool, float, int, str, Bridge PC ↔ app "CCPS4 Agent Viewer" no PS4 (fase 6, opcional, liga com PS4_BRID, build_homebrew() (+25 more)

### Community 23 - "Community 23"
Cohesion: 0.09
Nodes (35): Context, attach(), confirm(), detach(), download_file(), get_app(), get_messages(), install_pkg() (+27 more)

### Community 24 - "Community 24"
Cohesion: 0.19
Nodes (5): ftps4_line(), Ftps4Handler, Path, str, Comportamento do ftps4: sem SIZE/MLSD, LIST de caminho inexistente lista a pasta

### Community 25 - "Community 25"
Cohesion: 0.21
Nodes (11): _bool(), discover_ps4(), _int(), float, Path, str, Configuração do ps4-mcp, lida de variáveis de ambiente (definidas no .mcp.json)., Acha o PS4 na rede local. Tenta o DDP em broadcast e, sem resposta, varre a /24 (+3 more)

### Community 26 - "Community 26"
Cohesion: 0.24
Nodes (16): httpc_step(), drain_udp(), fill_addr(), net_format_ipv4(), net_last_error(), net_parse_ipv4(), net_recv(), net_send() (+8 more)

### Community 27 - "Community 27"
Cohesion: 0.31
Nodes (15): end_of_headers(), fail(), http_build_get(), http_build_post(), http_resp_feed(), http_resp_finish(), http_resp_init(), lower() (+7 more)

### Community 28 - "Community 28"
Cohesion: 0.29
Nodes (12): canvas_t, viewer_config_t, load_config(), main(), pad_read(), pad_try_open(), plat_ime_open(), plat_log() (+4 more)

### Community 29 - "Community 29"
Cohesion: 0.31
Nodes (8): int, str, announce(), clean(), live(), main(), make_handler(), Stub

### Community 30 - "Community 30"
Cohesion: 0.27
Nodes (11): crash_here(), log_line(), main(), notify(), off_of(), refresh_heap_copies(), align_up(), fill_rect() (+3 more)

### Community 31 - "Community 31"
Cohesion: 0.41
Nodes (10): install_openorbis.sh script, archive_ok(), ensure_openssl11(), fail(), finalize(), log(), need(), pkg_smoke_test() (+2 more)

### Community 32 - "Community 32"
Cohesion: 0.24
Nodes (9): FakePS4Debug, _demo_code(), main(), Run the fake ps4debug server with a demo console.      uv run python -m tests.fa, increment_counter(): push rbp; mov rbp,rsp; mov eax,[rip+c]; add eax,1; mov [rip, _repl(), bytes, int (+1 more)

### Community 33 - "Community 33"
Cohesion: 0.35
Nodes (6): bytes, int, str, main(), Img, load_font()

### Community 34 - "Community 34"
Cohesion: 0.38
Nodes (12): free_port(), int, readers(), test_file_follow_append_truncate_rotate(), test_file_missing_then_created(), test_file_starts_from_last_64k(), test_maxlen_and_long_lines(), test_tail_grep_regex_and_lines_since() (+4 more)

### Community 35 - "Community 35"
Cohesion: 0.47
Nodes (9): failf(), finish_ok(), httpc_abort(), httpc_busy(), httpc_init(), httpc_start(), httpc_t, net_close() (+1 more)

### Community 36 - "Community 36"
Cohesion: 0.20
Nodes (9): Arquitetura, CCPS4 Agent Viewer (fase 6, opcional), Configuração (opcional): `/data/ccps4/viewer.cfg`, Controles, Instalar, Limitações (leia antes), O que foi validado e o que não foi, Protocolo bridge v1 (resumo) (+1 more)

### Community 38 - "Community 38"
Cohesion: 0.25
Nodes (7): Build, CCPS4 Crash Test (alvo da fase 3), Instalar no PS4 (GoldHEN), Limitações conhecidas, O que ele faz, Offsets e endereço em tempo de execução, Procedimento do teste da fase 3

### Community 39 - "Community 39"
Cohesion: 0.25
Nodes (7): Checklist do piloto (seção 11.4) com as ferramentas do ps4-mcp, Em memória, Estrutura do arquivo, Fonte e glifos, KH3: formato das legendas (piloto da seção 11), Limites, Onde ficam

### Community 40 - "Community 40"
Cohesion: 0.29
Nodes (5): page_round(), page_trunc(), Add a vm_map entry. Bounds are rounded to 16 KiB pages (vm_map entries are page, Map each PT_LOAD segment at base+p_vaddr (page aligned), prot from p_flags, file, Raw write (ignores protections). Raises ValueError if any byte is unmapped.

### Community 41 - "Community 41"
Cohesion: 0.25
Nodes (7): Configuração (`.mcp.json` → env), Estado por fase, Estrutura, Primeiro uso com o console, PS4 Agent (CC-PS4), Segurança (seção 8), Testar sem o PS4

### Community 42 - "Community 42"
Cohesion: 0.29
Nodes (6): 1. Caso investigado, 2. Como rodar, 3. Métricas, 4. O que funcionou e o que atrapalhou, 5. Decisão, Relatório da fase 5: teste de valor

### Community 43 - "Community 43"
Cohesion: 0.52
Nodes (6): bytes, int, str, main(), read_ppm(), write_png()

### Community 45 - "Community 45"
Cohesion: 0.43
Nodes (6): int, str, find_fault_insn(), main(), parse_elf(), Acha, no disassembly (sintaxe Intel) de crash_here, a leitura de 32 bits de p->v

### Community 46 - "Community 46"
Cohesion: 0.40
Nodes (5): fmt_s(), main(), float, int, str

### Community 47 - "Community 47"
Cohesion: 0.47
Nodes (5): bytearray, bytes, int, main(), png_bytes()

### Community 48 - "Community 48"
Cohesion: 0.40
Nodes (4): Comportamentos que o cliente precisa respeitar, Formato, Protocolo do ps4debug: o que foi conferido no código-fonte, Sinais (FreeBSD)

## Knowledge Gaps
- **118 isolated node(s):** `/home/luizp2/Desktop/CC-PS4/.venv/bin/python`, `PS4_IP`, `PS4_WORKDIR`, `PS4_LOG_SOURCE`, `PS4_BRIDGE` (+113 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `FakePS4Debug` connect `Community 6` to `Community 32`, `Debug Events & RE Link`, `Safety & Write Gating`, `Community 11`, `Community 12`, `Community 14`?**
  _High betweenness centrality (0.169) - this node is a cross-community bridge._
- **Why does `LogReader` connect `Community 8` to `Community 34`, `Community 18`, `Community 19`, `Community 20`, `Community 21`, `Community 23`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `PS4Debug` connect `Debug Events & RE Link` to `Community 8`, `Community 13`, `Community 15`, `Community 18`, `Community 19`, `Community 20`, `Community 21`, `Community 23`?**
  _High betweenness centrality (0.061) - this node is a cross-community bridge._
- **Are the 11 inferred relationships involving `FakePS4Debug` (e.g. with `FakePS4Debug` and `bytes`) actually correct?**
  _`FakePS4Debug` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `str` (e.g. with `Bridge` and `PS4Debug`) actually correct?**
  _`str` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 29 inferred relationships involving `PS4Debug` (e.g. with `Context` and `OSError`) actually correct?**
  _`PS4Debug` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `DebugSession` (e.g. with `Context` and `Connection`) actually correct?**
  _`DebugSession` has 16 INFERRED edges - model-reasoned connections that need verification._