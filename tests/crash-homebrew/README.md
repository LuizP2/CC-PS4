# CCPS4 Crash Test (alvo da fase 3)

Homebrew mínimo em C para validar o ciclo de debug do `ps4-mcp`: breakpoint,
watchpoint de hardware, busca de texto na memória e um crash de ponteiro nulo
sempre no mesmo lugar.

| | |
|---|---|
| Título | CCPS4 Crash Test |
| TITLE_ID | `CCPS00002` |
| CONTENT_ID | `IV0000-CCPS00002_00-CCPS4CRASHTEST00` |
| Processo no console | `eboot.bin` |

## O que ele faz

1. Ao iniciar, loga (klog via `sceKernelDebugOutText(0, ...)` e também `printf`):
   ```
   [CCPS4-CRASHTEST] crash_here=0x... g_watch_counter=0x... g_subtitle_utf8=0x... g_subtitle_utf16=0x... heap_copy=0x...
   [CCPS4-CRASHTEST] image_base=0x... (eboot.bin, __text_start) heap_copy16=0x... main=0x...
   [CCPS4-CRASHTEST] offsets: crash_here=0x20 g_watch_counter=0x141e0 ...
   ```
   e mostra uma notificação "CCPS4 Crash Test: crash proposital em 90 s".
2. Por 90 s, uma vez por segundo: incrementa `volatile uint32_t g_watch_counter`
   (alvo do watchpoint), recopia a legenda para dois buffers no heap (UTF-8 e
   UTF-16LE) e loga `[CCPS4-CRASHTEST] tick N/90 counter=N`. Na tela, uma barra
   de progresso (os últimos 10 segmentos ficam amarelos).
3. Loga `chamando crash_here(NULL) agora`, pinta a tela de vermelho e chama
   `crash_here(NULL)`, que lê `p->value` (offset 8) → **SIGSEGV** lendo o endereço `0x8`.

Globais usadas no teste:

| Símbolo | Onde fica | Para quê |
|---|---|---|
| `crash_here` | `.text` | breakpoint de software e local do crash |
| `g_watch_counter` | `.bss` (gravável) | watchpoint de escrita, 4 bytes |
| `g_subtitle_utf8` = `"CCPS4 legenda de teste: Olá, ação, coração!"` | `.rodata` (só leitura) | busca de texto UTF-8 |
| `g_subtitle_utf16` (mesma frase em `char16_t`) | `.rodata` | busca de texto UTF-16LE |
| `g_heap_copy` / `g_heap_copy16` | ponteiros em `.bss` → buffers no heap | a mesma frase em memória **gravável** |

`crash_here` é `noinline`, o build é `-O0 -fno-omit-frame-pointer` e o ponteiro
nulo passa por uma variável `volatile`: o compilador não consegue remover nem
"adiantar" o acesso. Disassembly real (`build/crash_here.disasm.txt`):

```
0x20 <crash_here>: push rbp
0x21:              mov  rbp, rsp
0x24:              mov  qword ptr [rbp - 0x8], rdi      ; p (= NULL)
0x28:              mov  rax, qword ptr [rbp - 0x8]
0x2c:              add  rax, 0x8                       ; &p->value = 0x8
0x30:              mov  qword ptr [rbp - 0x10], rax
0x34:              mov  rax, qword ptr [rbp - 0x10]
0x38:              mov  eax, dword ptr [rax]           ; <- SIGSEGV (rax = 0x8)
```

## Build

```bash
scripts/install_openorbis.sh                 # na raiz do repo; imprime o export abaixo
export OO_PS4_TOOLCHAIN=$HOME/tools/OpenOrbis/PS4Toolchain
make -C tests/crash-homebrew                 # make clean para recomeçar
```

Requer `clang`, `ld.lld`, `llvm-nm`, `llvm-objdump` e `python3`. O `PkgTool.Core`
do OpenOrbis é .NET Core 3.0: o Makefile já o chama com
`DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` e, se o sistema só tiver OpenSSL 3, com
`LD_LIBRARY_PATH` apontando para as libs OpenSSL 1.1 que o script de instalação
coloca em `~/tools/OpenOrbis/openssl-1.1/lib`.

Saídas em `build/`:

| Arquivo | Conteúdo |
|---|---|
| `IV0000-CCPS00002_00-CCPS4CRASHTEST00.pkg` | o pacote para instalar (fake PKG, ~6,3 MB) |
| `crashtest.elf` | ELF com símbolos e DWARF (para o Ghidra/REA) |
| `pkg/eboot.bin` | o mesmo programa como fake SELF (o que roda no console) |
| `symbols.txt` | `llvm-nm -n` do ELF |
| `offsets.txt` | offset de cada símbolo-chave em relação à base da imagem |
| `crash_here.disasm.txt` | disassembly de `crash_here` |

## Offsets e endereço em tempo de execução

O `link.x` do OpenOrbis coloca o `.text` no **endereço de link 0** (o símbolo
`__text_start` vale 0 e o primeiro `PT_LOAD`, R-X, tem `p_vaddr = 0`). O ELF é
PIE, então no console ele é mapeado numa base qualquer (pode mudar a cada
execução). Por isso:

```
endereço_runtime = base_do_eboot.bin + offset
module_offset    = rip - base_do_eboot.bin      (o que o snapshot do ps4-mcp mostra)
offset           = st_value do símbolo no ELF   (porque a base de link é 0)
```

Valores deste build (confira sempre `build/offsets.txt`; mudam se o código mudar):

| Símbolo | offset | alvo |
|---|---|---|
| `crash_here` | `0x20` | `eboot.bin+0x20` |
| instrução do crash | `0x38` | `eboot.bin+0x38` (`crash_here+0x18`) |
| `main` | `0x40` | `eboot.bin+0x40` |
| `g_subtitle_utf8` | `0x4e90` | `eboot.bin+0x4e90` |
| `g_subtitle_utf16` | `0x4ed0` | `eboot.bin+0x4ed0` |
| `g_watch_counter` | `0x141e0` | `eboot.bin+0x141e0` |

A segunda linha do banner (`image_base=...`) mostra a base real e a terceira
mostra os offsets calculados no próprio console (`endereço − __text_start`):
se baterem com a tabela, a conta do agente está certa.

**No Ghidra/REA:** importe `build/crashtest.elf`. Para PIE com base 0 o Ghidra
pode aplicar outra *image base* (confira em *Window → Memory Map*); o endereço
no Ghidra é `image_base_do_Ghidra + offset`. Ajustando a image base para 0, o
endereço do Ghidra é o próprio offset; ajustando para a base vista no console,
bate com os endereços do snapshot.

## Instalar no PS4 (GoldHEN)

1. Copie o `.pkg` para um pendrive (FAT32/exFAT) ou para `/data/pkg/` por FTP.
2. Instale pelo instalador de pacotes do GoldHEN (*Debug Settings → Package
   Installer* aparece com o GoldHEN ativo) ou pelo instalador remoto que você já usa.
3. O ícone "CCPS4 Crash Test" aparece na tela inicial. Cada execução dura ~90 s e
   termina no crash (o sistema mostra o erro e fecha o app; é esperado).

## Procedimento do teste da fase 3

Pré-requisitos: payload ps4debug carregado, `ps4-mcp` registrado no Claude Code
e, se possível, o leitor de logs do `ps4-mcp` ligado (para ver o banner).
Você tem 90 s depois de abrir o app; com o processo pausado no debugger o
relógio do teste também para.

1. Abra **CCPS4 Crash Test** no console.
2. `ps4_status` → app em primeiro plano com title id `CCPS00002`.
3. `list_processes` → anote o `pid` de `eboot.bin`.
4. `attach(pid)`.
5. `set_breakpoint("eboot.bin+0x20")` (offset de `crash_here` em `build/offsets.txt`).
6. `set_watchpoint(<endereço de g_watch_counter>, 4, "write")` — o endereço é o
   `g_watch_counter=0x...` do banner, ou `base_do_eboot.bin + 0x141e0`.
7. `wait_for_event(timeout_s=10)` → **watchpoint** em até 1 s: `module_offset`
   dentro de `main` (a instrução logo depois da escrita em `g_watch_counter`);
   `read_memory` no endereço mostra o contador. Depois `resume`. Remova o
   watchpoint (`clear_breakpoint(id)` ou a ferramenta equivalente do `ps4-mcp`)
   para ele não disparar a cada segundo.
8. Busca de texto (com o processo pausado ou rodando):
   - `search_memory(pid, "Olá, ação", "utf-8")` → pelo menos 2 resultados:
     `eboot.bin+0x4e90` (+ deslocamento até "Olá") em `.rodata` e o buffer do heap
     (`heap_copy` do banner), que é memória gravável.
   - `search_memory(pid, "Olá, ação", "utf-16le")` → `g_subtitle_utf16` e `heap_copy16`.
   (Os nomes exatos dos parâmetros dependem do `ps4-mcp`; a ideia é UTF-8 e UTF-16.)
9. `resume` e `wait_for_event(timeout_s=120)` → no fim dos 90 ticks dispara o
   **breakpoint** em `eboot.bin+0x20` com `rdi = 0` (o argumento `p` é NULL).
10. `resume` de novo → evento de **crash** (SIGSEGV): `module_offset = 0x38`
    (`crash_here+0x18`), `rax = 0x8`, instrução `mov eax, dword ptr [rax]`.

**Pronto quando** (plano, fase 3): o agente aponta sozinho a função
`crash_here` e a causa — leitura de `p->value` (offset 8) com `p == NULL`
vindo de `main`.

## Limitações conhecidas

- Nada disto foi executado num PS4 ainda (não havia console disponível). O que
  foi validado no PC está listado em `ps4-app/README.md` e no relatório.
- A barra de progresso usa VideoOut/memória direta como o sample `graphics` do
  OpenOrbis; se o vídeo falhar, o teste segue só com logs (`video: indisponivel`).
- A notificação usa `sceKernelSendNotificationRequest` (padrão de vários
  homebrews); se não aparecer, não afeta o teste.
