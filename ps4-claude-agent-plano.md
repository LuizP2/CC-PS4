# PS4 Agent — Claude Code com acesso ao PS4 em tempo de execução

Plano de implementação

## 1. Objetivo

Dar ao Claude Code acesso direto ao PS4 durante a execução de um port, para que ele investigue bugs **sozinho, em loop**:

- navegar pela estrutura de pastas do console e do jogo instalado;
- ler a memória do processo do jogo enquanto ele roda;
- colocar breakpoints e watchpoints e ser avisado quando disparam;
- ler registradores, threads e logs no momento do erro;
- cruzar tudo isso com o binário (REA + Ghidra/IDA) e com o código-fonte do port.

A diferença para o fluxo atual: hoje alguém lê o relatório do ps4debug e cola um trecho num prompt. Aqui o agente **decide o que ler, lê, interpreta e decide a próxima leitura**, quantas vezes forem necessárias.

### O que este projeto NÃO reimplementa

O ps4debug (ou ps4debug-NG) já fornece as primitivas de debug no console. O projeto usa essas primitivas como base e adiciona a camada que falta: ferramentas MCP que o Claude Code consegue chamar, e a orquestração de eventos.

---

## 2. Arquitetura

```
┌──────────────────────── PC ─────────────────────────┐         ┌──────── PS4 (desbloqueado) ────────┐
│                                                     │         │                                    │
│  Claude Code                                        │         │  ps4debug / ps4debug-NG payload    │
│   ├── projeto do port (código, build, git)          │         │   - processos, memória, mapas      │
│   ├── REA MCP ──► Ghidra / IDA (binários dumpados)  │         │   - breakpoints, watchpoints       │
│   └── ps4-mcp  (este projeto)                       │  LAN    │   - registradores, threads         │
│         ├── cliente ps4debug  ──────────────────────┼────────►│                                    │
│         ├── cliente FTP       ──────────────────────┼────────►│  servidor FTP (ex.: do GoldHEN)    │
│         ├── leitor de logs    ──────────────────────┼────────►│  saída de log do sistema/jogo      │
│         └── fila de eventos (breakpoint/crash)      │         │                                    │
│                                                     │         │  jogo em execução (eboot.bin)      │
└─────────────────────────────────────────────────────┘         └────────────────────────────────────┘
```

**Por que o loop roda no PC:** o Claude Code depende de Node.js, shell, git e das ferramentas do PC. No PC ele mantém tudo isso (subagentes, acesso ao código do port, REA) e ainda age no PS4 pelas ferramentas. Um agente próprio rodando no console perderia esses recursos sem ganhar acesso extra ao console.

**Interface no PS4 (opcional, fase 6):** um app no console que mostra o que o agente está fazendo e permite mandar mensagens, para quem quiser operar do sofá. Não é necessário para validar.

---

## 3. Pré-requisitos

| Item | Observação |
|---|---|
| PS4 desbloqueado | Firmware compatível com a versão do payload escolhida |
| Payload de debug | ps4debug ou ps4debug-NG. ⚠️ Conferir qual versão suporta o seu firmware |
| Servidor FTP no console | ⚠️ Confirmar qual está disponível no seu setup (ex.: o do GoldHEN) e a porta |
| Saída de log | ⚠️ Confirmar como capturar logs do sistema/jogo no seu setup (servidor de klog, stdout do homebrew etc.) |
| PC | Python 3.11+, Claude Code, REA + Ghidra (ou IDA licenciado) |
| Cliente ps4debug | Biblioteca Python `ps4debug` (PyPI). ⚠️ Os nomes de métodos usados abaixo vêm da documentação do pacote e devem ser conferidos na versão instalada |

---

## 4. Catálogo de ferramentas do `ps4-mcp`

Separadas por nível de risco. **O MVP só habilita as de leitura.**

### 4.1 Leitura (MVP)

| Ferramenta | Faz | Base |
|---|---|---|
| `ps4_status` | Console conectado? Qual app está em primeiro plano (pid, title id, versão) | ps4debug |
| `list_processes` | Lista pid + nome | ps4debug |
| `process_maps(pid)` | Regiões de memória, proteções, nome do módulo | ps4debug |
| `read_memory(pid, addr, size)` | Lê até N bytes e devolve hex + ASCII | ps4debug |
| `read_pointer_chain(pid, base, offsets[])` | Segue ponteiros, devolvendo cada passo | ps4debug (implementado no MCP) |
| `read_string(pid, addr, max)` | Lê string terminada em zero | ps4debug |
| `list_dir(path)` | Lista uma pasta do console | FTP |
| `read_file(path, max_bytes)` | Lê arquivo do console (texto ou hex) | FTP |
| `download_file(path)` | Baixa para o PC (ex.: binário para o Ghidra) | FTP |
| `tail_logs(n)` | Últimas N linhas de log | leitor de logs |

### 4.2 Debug (fase 3)

| Ferramenta | Faz |
|---|---|
| `attach(pid)` / `detach()` | Inicia/encerra sessão de debug |
| `set_breakpoint(addr)` / `clear_breakpoint(id)` | Breakpoint de software |
| `set_watchpoint(addr, size, mode)` | Watchpoint de hardware (leitura/escrita) |
| `wait_for_event(timeout_s)` | **Bloqueia até um breakpoint/watchpoint/crash disparar** e devolve o snapshot |
| `get_registers(thread_id)` | Registradores da thread |
| `list_threads()` | Threads do processo |
| `step(thread_id)` | Executa uma instrução |
| `resume()` / `pause()` | Controla o processo |

### 4.3 Escrita (desabilitadas por padrão)

`write_memory`, `kill_process`, acesso a memória de kernel. Só habilitar com confirmação manual por chamada. Um agente escrevendo em memória ou no kernel pode travar o console.

### 4.4 Formato do snapshot de evento

Quando algo dispara, o MCP já monta um pacote rico, para o agente não precisar de dez chamadas só para entender o contexto:

```json
{
  "event": "breakpoint | watchpoint | crash",
  "pid": 123,
  "thread_id": 456,
  "rip": "0x0000000000412abc",
  "module": "eboot.bin",
  "module_offset": "0x12abc",
  "registers": { "rax": "...", "rbx": "...", "rsp": "...", "...": "..." },
  "stack_bytes": "hex dos 256 bytes a partir de rsp",
  "code_bytes": "hex dos 64 bytes ao redor do rip",
  "recent_logs": ["...últimas 30 linhas..."]
}
```

O `module_offset` é o que liga o evento ao Ghidra/REA: o agente abre o binário dumpado nesse offset e lê a função.

---

## 5. Esqueleto do servidor MCP

```python
# ps4_mcp/server.py
import os
from mcp.server.fastmcp import FastMCP
from ps4debug import PS4Debug  # ⚠️ conferir API na versão instalada

PS4_IP = os.environ["PS4_IP"]
MAX_READ = 64 * 1024

mcp = FastMCP("ps4")
ps4 = PS4Debug(PS4_IP)


def hexdump(data: bytes, base: int) -> str:
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base + i:016x}  {hx:<47}  {asc}")
    return "\n".join(lines)


@mcp.tool()
async def list_processes() -> list[dict]:
    """Lista os processos em execução no PS4 (pid e nome)."""
    procs = await ps4.get_processes()
    return [{"pid": p.pid, "name": p.name} for p in procs]


@mcp.tool()
async def process_maps(pid: int) -> list[dict]:
    """Regiões de memória do processo: início, fim, proteção e nome."""
    maps = await ps4.get_process_maps(pid)
    return [m.__dict__ for m in maps]  # ⚠️ ajustar aos campos reais


@mcp.tool()
async def read_memory(pid: int, address: str, size: int) -> str:
    """Lê memória do processo. address em hex (ex.: '0x412abc'). Máximo 64 KiB."""
    addr = int(address, 16)
    size = min(size, MAX_READ)
    data = await ps4.read_memory(pid, addr, size)
    return hexdump(data, addr)


if __name__ == "__main__":
    mcp.run()
```

Registro no Claude Code (⚠️ conferir a sintaxe com `claude mcp --help`):

```bash
claude mcp add ps4 -e PS4_IP=192.168.0.50 -- python -m ps4_mcp.server
```

---

## 6. Eventos em "tempo real"

O modelo leva segundos por resposta, então não vigia a memória continuamente. O fluxo é:

1. O agente coloca breakpoints/watchpoints nos pontos suspeitos.
2. O agente chama `wait_for_event(timeout_s=120)`.
3. O jogo roda. Quando algo dispara, o payload pausa o processo e manda o evento.
4. O MCP monta o snapshot (seção 4.4) e devolve para o agente.
5. O agente investiga com o processo **pausado**: lê mais memória, segue ponteiros, abre o offset no Ghidra/REA, compara com o código do port.
6. Decide: novo breakpoint, `resume`, ou conclusão com hipótese e correção proposta.

Implementação no MCP: uma tarefa em segundo plano mantém a conexão de interrupções do payload e coloca eventos numa `asyncio.Queue`; `wait_for_event` só consome dessa fila.

> ⚠️ Conferir o tempo limite de chamadas de ferramenta MCP no Claude Code. Se for curto, `wait_for_event` usa timeout menor e o agente chama de novo.

**Limitação:** pausar o processo altera o timing. Bugs de condição de corrida podem sumir quando observados.

---

## 7. Fases

### Fase 0 — Ambiente e primitivas (sem IA)
- Carregar o payload de debug no console.
- Pelo cliente Python, num script simples: listar processos, ler 64 bytes de um endereço válido do jogo.
- Confirmar FTP e captura de logs.

**Pronto quando:** um script Python no PC lista processos e lê memória do jogo rodando.

### Fase 1 — MCP de leitura
- Implementar as ferramentas da seção 4.1.
- Registrar no Claude Code.

**Pronto quando:** pedindo "qual jogo está rodando e quais módulos estão carregados?", o Claude Code responde usando as ferramentas, sem ajuda.

### Fase 2 — Sistema de arquivos e logs
- `list_dir`, `read_file`, `download_file`, `tail_logs`.

**Pronto quando:** o agente acha sozinho um arquivo de configuração/asset do jogo instalado e explica sua estrutura.

### Fase 3 — Debug e eventos
- Ferramentas da seção 4.2, fila de eventos, snapshot.

**Pronto quando:** num homebrew de teste com um crash proposital (ex.: ponteiro nulo), o agente coloca breakpoint, recebe o evento e aponta a função e a causa.

### Fase 4 — Integração com RE
- Instalar o REA no mesmo Claude Code.
- Baixar o binário via `download_file` e abrir no Ghidra pelo REA.
- Usar `module_offset` do snapshot para ir direto à função.

**Pronto quando:** a partir de um crash, o agente mostra o pseudocódigo da função onde ocorreu e explica o problema.

⚠️ Verificar se o REA/Ghidra lê o binário na forma em que ele sai do console (ex.: ELF já descriptografado). Isso precisa ser testado nesta fase.

### Fase 5 — Teste de valor (decide se o projeto continua)
- Projeto piloto: tradução das legendas do Kingdom Hearts 3 (seção 11).
- Alternativa: escolher **um bug real** de um port em andamento, ainda não resolvido.
- Rodar a investigação com o agente e registrar: tempo até a causa, número de chamadas, se a hipótese estava certa.
- Comparar com o tempo que o mesmo tipo de bug costuma levar no fluxo manual.

**Pronto quando:** existe um relatório curto com esses números. Ele decide se vale continuar e é o material para mostrar no fórum.

### Fase 6 — Opcional
- App no PS4 que mostra a atividade do agente e permite mandar mensagens (teclado nativo, USB ou teclado na tela).
- Ferramentas de build/deploy: compilar com OpenOrbis e instalar o `.pkg` pelo agente.
- Subagentes por área do port (gráfico, áudio, input), só depois que o fluxo de um agente estiver sólido.

---

## 8. Segurança

- Tudo só na rede local. Nenhuma porta aberta no roteador.
- Ferramentas de escrita desligadas por padrão (seção 4.3).
- Limites no MCP: tamanho máximo de leitura, número máximo de breakpoints, timeout.
- Log de toda chamada de ferramenta (o que foi lido, de onde), para auditoria.
- Usar apenas em software que você tem autorização para analisar, e ferramentas comerciais (IDA) só com licença.

---

## 9. Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Payload não suporta o firmware | Projeto travado | Fase 0 valida antes de qualquer código |
| Kernel panic durante debug | Console reinicia, sessão perdida | Só leitura no MVP; poucos breakpoints; salvar snapshots no PC |
| API do cliente Python diferente do esperado | Retrabalho no MCP | Fase 0 usa o cliente direto antes do MCP |
| Timeout de ferramenta curto | `wait_for_event` falha | Timeout menor + chamadas repetidas |
| Pausar altera o bug | Bug não reproduz | Usar watchpoints e logs em vez de breakpoints para bugs de timing |
| Custo de tokens alto | Uso caro | Snapshots compactos, leituras limitadas, resumo de hexdumps grandes |
| REA não lê o binário | Sem pseudocódigo | Testar na fase 4; fallback: abrir no Ghidra manualmente |

---

## 10. Modelo de execução: o agente fica vivo durante o jogo

| Componente | Onde roda | O console fecha ao abrir um jogo? |
|---|---|---|
| Claude Code + `ps4-mcp` + REA/Ghidra | PC | Não. Roda fora do console |
| Payload de debug | PS4, em segundo plano | Não. É carregado uma vez e fica ativo enquanto o jogo roda |
| App de interface (fase 6, opcional) | PS4, primeiro plano | **Sim, é suspenso.** Não funciona como o Spotify, que é um serviço integrado pela Sony |

Fluxo de uso:
1. Carregar o payload de debug no console.
2. Iniciar o jogo.
3. No PC, abrir o Claude Code e pedir para se conectar ao processo do jogo.
4. Jogar normalmente enquanto o agente investiga. O acompanhamento é feito no PC (ou no celular, por acesso remoto ao PC).

Uma interface por cima do jogo exigiria um overlay. ⚠️ Não há confirmação de um método estável para isso no PS4; fica fora do escopo.

---

## 11. Projeto piloto: tradução das legendas do Kingdom Hearts 3

### 11.1 Objetivo

Usar o agente para viabilizar uma tradução das legendas para PT-BR, distribuída **apenas como patch** (nunca com arquivos do jogo), a partir de uma cópia própria e legítima do jogo.

### 11.2 Onde o agente ajuda (e onde não)

O texto das legendas está nos **arquivos do jogo** (pacotes de localização), não na memória. A tradução em si é: extrair → traduzir → reempacotar. O agente em tempo real entra como ferramenta de investigação:

| Problema | Como o agente ataca | Fases usadas |
|---|---|---|
| De qual arquivo vem esta fala? | Lê a memória quando a legenda está na tela, acha a string e cruza com os arquivos extraídos | 2, 3 |
| Qual o formato/codificação do texto? | Compara a string na memória com os bytes no arquivo; identifica UTF-8/UTF-16, códigos de controle, tabelas de offset | 2, 4 |
| Por que os acentos (ç, ã, é) não aparecem? | Verifica se a fonte do jogo tem os glifos; localiza a fonte nos arquivos e a rotina de renderização no binário | 2, 3, 4 |
| Texto traduzido maior quebra o jogo | Watchpoint no buffer da legenda; investiga limites de tamanho e ponteiros | 3, 4 |
| Patch não carrega | Confere o caminho e a estrutura de pastas do arquivo modificado no console | 2 |

**Fora do agente:** a tradução do texto em si (pode usar IA, mas é revisão humana que garante qualidade), e o empacotamento final.

> Hipótese a verificar, não confirmada: o KH3 foi feito em Unreal Engine 4, e jogos nessa engine costumam guardar texto localizado em arquivos de localização dentro de pacotes `.pak`. ⚠️ Confirmar na prática onde ficam as legendas desse jogo antes de planejar a extração.

### 11.3 Ferramentas extras para este piloto

| Ferramenta | Faz | Base |
|---|---|---|
| `search_memory(pid, text, encoding)` | Procura uma frase (ex.: a legenda visível) na memória do jogo, em UTF-8 e UTF-16 | varredura de memória do payload |
| `read_text_region(pid, addr, size, encoding)` | Lê um bloco e decodifica como texto, mostrando códigos de controle | ps4debug |
| `search_files(root, text, encoding)` | Procura a mesma frase nos arquivos extraídos do jogo no PC | local |
| `compare_bytes(memory_hex, file_path, offset)` | Compara o que está na memória com o arquivo, para entender a transformação | local |

Ficam no `ps4-mcp`; as que operam em arquivos extraídos rodam no PC.

### 11.4 Passo a passo do piloto

1. **Preparação:** cópia própria do jogo, arquivos extraídos numa pasta do PC para análise.
2. **Rastrear uma legenda:** com uma cena aberta e a legenda na tela, pausar e pedir ao agente: "encontre esta frase na memória e diga de qual arquivo ela vem".
3. **Mapear o formato:** o agente descreve a estrutura do arquivo (cabeçalho, tabela de strings, codificação). Resultado salvo em `docs/kh3-formato-legendas.md`.
4. **Teste mínimo:** trocar **uma** legenda por um texto curto sem acento, reempacotar, testar no console.
5. **Teste de acentos:** trocar por um texto com ç, ã, é. Se não aparecer, o agente investiga a fonte e a renderização.
6. **Teste de tamanho:** trocar por um texto bem maior que o original e observar o comportamento.
7. **Escala:** com o formato entendido, script de extração/reinserção em lote, e a tradução segue em paralelo.

### 11.5 Métricas para a Fase 5

- Tempo até identificar o arquivo de origem de uma legenda.
- Tempo até documentar o formato.
- Se o problema de acentos (caso exista) foi diagnosticado e quanto tempo levou.
- Número de chamadas de ferramenta e custo aproximado de tokens.
- Quantas hipóteses do agente estavam erradas (e se ele mesmo percebeu).

### 11.6 Riscos específicos

| Risco | Mitigação |
|---|---|
| Fonte sem glifos de acento | Investigar substituição/expansão da fonte; em último caso, transliterar |
| Limite fixo de tamanho de texto | Mapear limites cedo (passo 6) e adaptar a tradução |
| Arquivos com verificação de integridade | Investigar com o agente na fase 4 antes de reempacotar em lote |
| Distribuição | Publicar só o patch; nada dos arquivos originais |

---

## 12. Estrutura do repositório

```
ps4-agent/
├── ps4_mcp/
│   ├── server.py        # ferramentas MCP
│   ├── debug.py         # sessão de debug + fila de eventos
│   ├── ftp.py           # sistema de arquivos do console
│   ├── logs.py          # captura de logs
│   ├── snapshot.py      # montagem do snapshot de evento
│   └── safety.py        # limites, allowlist de ferramentas, auditoria
├── tests/
│   └── crash-homebrew/  # homebrew com crash proposital para a fase 3
├── docs/
│   ├── relatorio-fase5.md
│   └── kh3-formato-legendas.md
└── README.md
```
