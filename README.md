# PS4 Agent (CC-PS4)

Dá ao Claude Code acesso ao PS4 em tempo de execução para investigar bugs de ports sozinho, em loop: ler memória, colocar breakpoints e watchpoints, receber o evento quando disparam, ler registradores, threads, arquivos e logs, e cruzar tudo com o binário no Ghidra via ReVa. O plano está em [`ps4-claude-agent-plano.md`](ps4-claude-agent-plano.md).

O loop do agente roda no PC. No console ficam o payload ps4debug e, opcionalmente, o app **CCPS4 Agent Viewer**, que mostra o que o agente faz e manda mensagens do sofá.

```
Claude Code ── MCP stdio ──► ps4_mcp (PC) ──► ps4debug :744 / callback :755 (memória, debug)
                                  ├────────► FTP do GoldHEN :2121 (arquivos)
                                  ├────────► klog :3232 (logs)
                                  ├────────► bridge HTTP :8765 + UDP :8766 ◄── app CCPS4 Agent Viewer (PS4)
                                  └── dump_module → ELF → ReVa/Ghidra (pseudocódigo)
```

## Estado por fase

| Fase | O que existe | Validado sem PS4 |
|---|---|---|
| 0. Primitivas | `scripts/fase0_smoke.py` (lista processos, lê 64 bytes, testa FTP/klog), `scripts/send_payload.py` | contra o ps4debug falso |
| 1. MCP de leitura | `ps4_status`, `list_processes`, `process_maps`, `read_memory`, `read_pointer_chain`, `read_string`, `resolve_address`, `disassemble` | testes ponta a ponta via MCP stdio |
| 2. Arquivos e logs | `list_dir`, `read_file`, `download_file`, `tail_logs` (FTP + klog TCP/UDP/arquivo) | FTP real (pyftpdlib), klog falso |
| 3. Debug e eventos | `attach`/`detach`, `set_breakpoint`/`clear_breakpoint`, `set_watchpoint`/`clear_watchpoint`, `wait_for_event`, `get_registers`, `list_threads`, `step`, `resume`/`pause` + snapshot (seção 4.4) + PKG de teste `tests/crash-homebrew` | ps4debug falso fiel ao C + validação cruzada com a lib `ps4debug` do PyPI |
| 4. Integração com RE | `dump_module` (memória decifrada → ELF com endereços reais) + ReVa/Ghidra 12.1.3 instalados | ELF importado e decompilado no ReVa |
| 5. Teste de valor | `mark_milestone`, `scripts/metrics.py`, `docs/relatorio-fase5.md`, ferramentas do piloto KH3 (`search_memory`, `read_text_region`, `search_files`, `compare_bytes`), `docs/kh3-formato-legendas.md` | ferramentas testadas; o teste de valor em si exige o console |
| 6. Opcional | app `ps4-app/` (PKG), bridge (`post_status`, `get_messages`, mensagens chegam em `wait_for_event`), `notify_console`, `build_homebrew`/`install_pkg` | build dos PKGs, cliente do app compilado e testado no PC, instalação contra um RPI falso |

Fica de fora, como o próprio plano pede: subagentes por área do port (só depois que o fluxo de um agente estiver sólido) e overlay por cima do jogo (não há método estável). Para avisar quem está jogando, `notify_console` usa a notificação do sistema, que aparece por cima do jogo.

## Primeiro uso com o console

1. **No PS4:**
   - Com o GoldHEN, ative o servidor FTP (porta 2121) e o BinLoader (porta 9090).
   - Envie o payload ps4debug **da versão do seu firmware**: `uv run python scripts/send_payload.py ps4debug.bin --ip <IP do PS4>`.
   - Para logs, ative um servidor de klog na porta 3232 (ou ajuste `PS4_LOG_SOURCE`).
2. **No PC, uma vez:**
   ```bash
   echo 'export PS4_IP=192.168.0.50' >> ~/.zshrc         # opcional: sem ele o IP é descoberto na rede
   sudo sysctl -w net.ipv4.ip_unprivileged_port_start=755  # o ps4debug conecta de volta na porta 755 do PC
   echo 'net.ipv4.ip_unprivileged_port_start=755' | sudo tee /etc/sysctl.d/99-ps4debug.conf
   ```
   Se houver firewall, libere TCP 755 vindo do PS4 (e TCP 8765/UDP 8766 se for usar o app).
3. **Valide sem IA:** `uv run python scripts/fase0_smoke.py --ftp --logs`. Se terminar com OK, a fase 0 está pronta.
4. **No Claude Code:** reinicie a sessão nesta pasta e aprove o servidor `ps4` do `.mcp.json`. Peça "rode ps4_diagnose" e depois "qual jogo está rodando e quais módulos estão carregados?", que é o critério da fase 1.

> ⚠️ Se um ATTACH falhar por causa da porta 755 ou do firewall, o ps4debug fica preso em "debugging". É um bug do próprio payload. Reinicie o console e recarregue o payload. O `ps4_diagnose` existe para evitar isso.

## Configuração (`.mcp.json` → env)

| Variável | Padrão | Uso |
|---|---|---|
| `PS4_IP` | `auto` | IP do console. Vazio ou `auto`: descobre ao iniciar (DDP na porta 987; sem resposta, varre a /24 do PC atrás da porta 744). Só IPs privados são aceitos, salvo `PS4_ALLOW_PUBLIC_IP=1` |
| `PS4DEBUG_PORT` / `PS4DEBUG_CALLBACK_PORT` | 744 / 755 | portas do ps4debug (a 755 é fixa no payload) |
| `PS4_FTP_PORT` | 2121 | FTP do GoldHEN |
| `PS4_LOG_SOURCE` | `tcp:<PS4_IP>:3232` | `tcp:IP:PORTA`, `udp:BIND:PORTA`, `file:/caminho` ou `off` |
| `PS4_MAX_READ` / `PS4_INLINE_HEX_MAX` | 65536 / 1024 | limite de leitura e quanto vira hexdump inline (acima disso: arquivo + resumo) |
| `PS4_MAX_BREAKPOINTS` / `PS4_MAX_WAIT_S` | 10 / 300 | limites da seção 8 |
| `PS4_ENABLE_WRITE` | 0 | habilita `write_memory`, `kill_process` e `kernel_read`, sempre com confirmação manual |
| `PS4_ENABLE_DEPLOY` | 0 | habilita `build_homebrew` e `install_pkg` (instala via Remote Package Installer, porta 12800) |
| `PS4_BRIDGE` | 0 | liga o bridge do app CCPS4 Agent Viewer |
| `PS4_WORKDIR` | pasta do projeto | onde ficam `dumps/`, `downloads/` e `logs/audit.jsonl` |

## Segurança (seção 8)

- **Rede:** só rede local. IPs públicos são recusados e o bridge só atende o `PS4_IP`. Nenhuma porta precisa ser aberta no roteador.
- **Escrita:** as ferramentas de escrita, kernel e deploy nem são registradas sem a variável correspondente. Quando ligadas, pedem confirmação manual a cada chamada pela *elicitation* do MCP: o Claude Code mostra a pergunta para você responder.
- **Limites:** tamanho máximo de leitura, número de breakpoints, timeouts, watchpoints alinhados e recusa de breakpoint fora de código ou duplicado.
- **Auditoria:** toda chamada vai para `logs/audit.jsonl` (ferramenta, argumentos, duração, resultado).
- **Saída do MCP:** se o servidor MCP morrer, a conexão "dona" do ATTACH fecha. O payload então desanexa sozinho e restaura os bytes dos breakpoints.

## Testar sem o PS4

```bash
uv run pytest                                         # tudo; grave em arquivo por causa do resumo do RTK
uv run python -m tests.fakes.run_fake --port 7440 --debug-port 7550 --scenario demo
```

O ps4debug falso foi escrito a partir do código C do payload, com as mesmas peculiaridades. Para conversar com ele pelo Claude Code, rode o servidor com `PS4_IP=127.0.0.1 PS4DEBUG_PORT=7440 PS4DEBUG_CALLBACK_PORT=7550 PS4DEBUG_CALLBACK_BIND=127.0.0.1`.

## Estrutura

```
ps4_mcp/            servidor MCP: server.py (ferramentas), client.py/protocol.py (ps4debug), debug.py (sessão
                    e fila de eventos), snapshot.py, ftp.py, logs.py, sfo.py, text.py, elfdump.py, safety.py,
                    bridge.py (app do PS4), deploy.py (OpenOrbis + Remote Package Installer)
tests/              testes + fakes/ (ps4debug falso) + crash-homebrew/ (PKG de teste da fase 3)
ps4-app/            app CCPS4 Agent Viewer (fase 6)
scripts/            fase0_smoke.py, send_payload.py, metrics.py, install_openorbis.sh
docs/               protocolo-ps4debug.md, relatorio-fase5.md, kh3-formato-legendas.md
```
