# Protocolo do ps4debug: o que foi conferido no código-fonte

Fonte: [jogolden/ps4debug](https://github.com/jogolden/ps4debug), protocolo `"1.2"`, arquivos `debugger/include/{protocol,debug,proc,kdbg,net,server}.h` e `debugger/source/{server,debug,proc,console,kern,net}.c`. Forks para outros firmwares mantêm o mesmo formato de fio. Implementação no PC: `ps4_mcp/protocol.py`, `ps4_mcp/client.py` e `ps4_mcp/debug.py`.

## Formato

- **Porta de comandos:** 744. Cada pacote tem um cabeçalho de 12 bytes (`magic 0xFFAABBCC`, `cmd`, `datalen`), seguido de `datalen` bytes. Tudo é little-endian e as structs são `packed`.
- **Resposta:** um status de 4 bytes, onde `0x80000000` é sucesso. Em seguida vêm os dados do comando.
- **Exceção:** `CMD_VERSION` não manda status. Responde só com `u32` tamanho + `"1.2"`.

| Estrutura | Bytes | Campos |
|---|---|---|
| `proc_list_entry` | 36 | `char p_comm[32]`, `int pid` |
| `proc_vm_map_entry` | 58 | `char name[32]`, `u64 start`, `u64 end`, `u64 offset`, `u16 prot` |
| `cmd_proc_info_response` | 188 | `pid`, `name[40]`, `path[64]`, `titleid[16]`, `contentid[64]` |
| `__reg64` | 176 | r15…rax, `u32 trapno`, `u16 fs`, `u16 gs`, `u32 err`, `u16 es`, `u16 ds`, rip, cs, rflags, rsp, ss |
| `debug_interrupt_packet` | 0x4A0 | `lwpid`, `status` (wait4), `tdname[40]`, `__reg64`, `savefpu_ymm` (832), `__dbreg64` (128) |

## Comportamentos que o cliente precisa respeitar

1. **Erro derruba a conexão** (`server.c: handle_client`). Todo handler que responde erro retorna 1, e o servidor chama `free_client`. Se aquela conexão era a que fez o ATTACH, isso também desfaz o debug.
   - O cliente usa três conexões: uma de leitura, uma "dona" do ATTACH (fica parada) e uma para os comandos de debug.
   - Um erro num comando de debug derruba só a conexão de comandos, e a sessão continua.
2. **Callback na porta 755** (`debug.c: debug_attach_handle`/`connect_debugger`). No ATTACH, o PS4 conecta de volta no IP do PC, porta 755, antes de responder.
   - Se essa conexão falhar, `g_debugging` fica ligado para sempre. As próximas tentativas recebem `CMD_ALREADY_DEBUG` até o payload ser recarregado (reiniciar o console).
   - Por isso o PC abre o listener antes de enviar o ATTACH.
   - No Linux, portas abaixo de 1024 exigem `sysctl net.ipv4.ip_unprivileged_port_start=755`. A ferramenta `ps4_diagnose` confere.
3. **Comandos de debug sem sessão derrubam o payload** (ex.: `debug_breakpt_handle` lê `curdbgctx->pid` com `curdbgctx` nulo). O cliente bloqueia esses comandos sem attach e marca a sessão como morta assim que a conexão de interrupções fecha.
4. **Interrupções só no laço da conexão dona** (`server.c: check_debug_interrupt`). Os eventos só são verificados quando essa conexão está ociosa, então ela não é usada para mais nada. Fechar essa conexão faz o payload desanexar e restaurar o jogo, o que serve de rede de segurança se o MCP cair.
5. **Breakpoint de software.** Armar lê o byte original e escreve `0xCC`; desarmar devolve o original.
   - Armar duas vezes no mesmo endereço faria o payload guardar `0xCC` como "original" e corromper o código. O cliente recusa duplicatas e endereços que já contêm `0xCC`, e nunca repete automaticamente um BREAKPT.
   - Quando o breakpoint dispara, o payload restaura o byte, volta o `rip`, executa a instrução em single-step, rearma o `0xCC` e só então manda o pacote, com os registradores de antes da instrução.
   - Ler a memória com o breakpoint armado mostra `0xCC`. O `disassemble` e o snapshot substituem pelo byte original.
6. **Watchpoints** (DR0–DR3) são aplicados só às threads que existem no momento.
   - DR7 recebe o tamanho codificado: 1 → 0, 2 → 1, 4 → 3, 8 → 2. O tipo também: exec 0, write 1, rw 3.
   - O endereço precisa estar alinhado ao tamanho.
   - O trap acontece depois da instrução que fez o acesso, então o `rip` aponta para a seguinte.
7. **Pausar com SIGSTOP não gera evento**: `STOPGO(1)` não produz pacote de interrupção. `STOPGO(0)` continua e `STOPGO(2)` mata o processo.
8. **`PROC_MAPS` vem deslocado** (`kdebugger/source/hooks.c`). A varredura começa no sentinela do vm_map: a 1ª entrada é `("", 0x4000, 0x800000000000, prot 0)`, que o cliente descarta, e a última região real (a de endereço mais alto) não aparece. Ler ali funciona, mas `resolve_address`/avisos não a reconhecem.
9. **`PROC_READ` não informa erro.** O status de sucesso vai antes da leitura e memória não mapeada volta como zeros. O cliente confere os mapas e avisa.
10. **`CMD_PROC_SCAN` tem bugs.** Ele relê sempre a primeira página de cada região e avança pelo tamanho do valor buscado. A busca de texto (`search_memory`) é feita no PC, lendo a memória em blocos de 1 MiB.
11. **Aceite lento.** O laço de aceite dorme 2 s entre conexões (`sceKernelSleep(2)`), então abrir uma conexão nova pode levar até 2 s.
12. **Kernel.** O instalador remove checagens do `ptrace` no kernel (`installer.c`). É isso que permite `STOPGO` e `PT_GETREGS` com o processo rodando. Ler endereço inválido do kernel causa kernel panic, por isso `kernel_read` vem desligado.

13. **Processo morre com debugger anexado** (`server.c:86`). Depois do `debug_cleanup`, o payload usa `curdbgctx` nulo: quando o jogo fecha ou é morto durante a sessão, o ps4debug provavelmente crasha e precisa ser recarregado.

14. **ps4debug 1.3 (GoldHEN v1.1.19) no FW 12.52: o ATTACH deixa o processo parado.** O jogo congela até o detach. O cliente manda `STOPGO(0)` logo depois do ATTACH e ignora um eventual erro. Com isso, o crash do crash-test chegou como evento (`SIGSEGV` em `eboot.bin+0x38`).

15. **RPC dentro do processo** (`proc.c: proc_install_handle/proc_call_handle/proc_alloc_handle`, `rpcasm.h`). É o que o `scan_value` usa com `PS4_INGAME_SCAN=1`.
    - `PROC_INSTALL` (`CMD_PROC_INTALL` no C) aloca uma região, copia o stub de 410 bytes (magic `RSTB`, entrada em `0xA3`) e cria uma thread nele. Responde `u64` com o endereço do stub.
    - A thread só é criada se o processo tiver `libkernel*.sprx` r-x. Sem ela, o status ainda é sucesso.
    - A thread resolve `sceKernelUsleep` e grava o ponteiro em `stub+0x9B` antes do 1º laço. O cliente só chama o stub depois de ver esse campo diferente de zero. Também confere o hash do código do stub, porque com outro stub os offsets mudam.
    - `PROC_CALL` (`pid`, `stub`, `rip`, `rdi`…`r9`, 68 bytes) escreve os argumentos no stub, liga `rpc_go` e o **kernel espera `rpc_done` em laço**. Responde `pid` + `rax`.
    - Se a thread do stub não roda (não existe, processo parado por `SIGSTOP`, breakpoint ou watchpoint), esse laço nunca termina e o payload trava até reiniciar o console. Por isso o MCP não usa RPC com o processo anexado ao debugger.
    - A thread do stub dorme 100 ms entre verificações, então cada `PROC_CALL` custa até ~100 ms a mais.
    - `PROC_ALLOC` (`pid`, `length`) cria uma região rwx e responde `u64`. Em falha, devolve lixo: o cliente confere o endereço nos mapas. `PROC_FREE` (`pid`, `address`, `length`) desfaz a região. Erro nesses dois não derruba a conexão.
    - Um bug no código chamado derruba o jogo, não o console.

## Sinais (FreeBSD)

O `status` do pacote de interrupção é o valor do `wait4`. Para processo parado, `WSTOPSIG = (status >> 8) & 0xff`. No FreeBSD, SIGSTOP é 17 e SIGBUS é 10, diferente do Linux.

O cliente classifica os eventos assim:

| Evento | Condição |
|---|---|
| `breakpoint` | SIGTRAP com `rip` igual a um breakpoint |
| `watchpoint` | SIGTRAP com bit em DR6 |
| `step` | SIGTRAP após `step()` |
| `crash` | SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT ou SIGSYS |
