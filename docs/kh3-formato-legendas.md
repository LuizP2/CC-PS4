# KH3: formato das legendas (piloto da seção 11)

> Documento preenchido pelo agente durante o piloto. Use só uma cópia própria e legítima do jogo e distribua apenas o patch, nunca arquivos do jogo nem a chave AES. A hipótese do Unreal Engine 4 com textos em `.pak` foi **confirmada** em 2026-10-29 (KH3 EP `CUSA12025`, `EP0082-CUSA12025_00-KINGDOMHEARTSX30`, PS4 FW 12.52).

## Checklist do piloto (seção 11.4) com as ferramentas do ps4-mcp

1. **Preparação.** Extraia os arquivos do jogo numa pasta do PC e anote o caminho: `______`.
2. **Rastrear uma legenda.** Com a legenda na tela:
   - `attach()` e depois `pause()`;
   - `search_memory(pid, "<trecho exato da legenda>")`, que busca em UTF-8 e UTF-16LE;
   - `read_text_region(pid, <endereço>, 512, "utf-16le")` para ver os códigos de controle ao redor;
   - `search_files("<pasta extraída>", "<mesmo trecho>")` para achar o arquivo de origem;
   - `compare_bytes("<arquivo>", <offset>, pid=..., address=..., size=...)` para entender a transformação;
   - `mark_milestone("arquivo_encontrado")`.
3. **Mapear o formato.** Preencha as seções abaixo e registre `mark_milestone("formato_documentado")`.
4. **Teste mínimo.** Troque uma legenda por texto curto sem acento, reempacote e teste no console.
5. **Teste de acentos.** Troque por um texto com ç, ã e é. Se falhar, investigue a fonte e a rotina de renderização com `set_breakpoint` e `disassemble`, e use `dump_module` no ReVa.
6. **Teste de tamanho.** Troque por um texto bem maior e observe. `set_watchpoint` no buffer da legenda mostra quem escreve e lê o texto.
7. **Escala.** Escreva o script de extração e reinserção em lote.

## Onde ficam

| Item | Valor |
|---|---|
| Arquivo de origem (no PC) | `TresGame/Content/Localization/Game/<idioma>/kh3_*.locres`, extraídos para fora do repositório |
| Caminho no console (`list_dir`) | `/mnt/sandbox/pfsmnt/CUSA12025-app0/tresgame/Content/Paks/pakchunk0-ps4.pak` (visível pelo FTP do GoldHEN com o jogo aberto) |
| Contêiner (pak/locres/outro) | `.pak` do UE4, versão 4, índice criptografado com AES-256-ECB. Dentro dele há `.locres` do UE4, versão 1. São 54 por idioma: `de`, `en`, `es`, `fr` e `it`. **Não há PT-BR.** |
| Verificação de integridade? | Cada entrada do índice do pak tem um SHA1 dos dados. Ainda não sabemos se o jogo confere esse hash ao carregar. Vale testar no passo 4. |

Arquivos de texto por área (inglês):

| Texto | Arquivo |
|---|---|
| Menus, tela de título, teatro, loja, síntese, savepoint | `kh3_menu.locres` (10 namespaces, 555 chaves, 522 strings) |
| Objetivos de fase ("Continue on and look for Boo's door!") | `kh3_level.locres` |
| Acampamento e favoritos | `kh3_camp.locres` |
| Cenas por mundo | `kh3_event_<mundo>.locres` (`bt`, `bx`, `ca`, `dw`, `fz`, `gm`, ...) |
| Falas de campo | `kh3_fieldvoice_<mundo>.locres` |
| Outros | `kh3_tutorial`, `kh3_item`, `kh3_system`, `kh3_gamehelp`, `kh3_map`, `kh3_minigame`, `kh3_gumiship`, `kh3_char_name`, `kh3_mobile`, `kh3_extraload` |

### Como chegar aos arquivos

1. **Chave AES do pak.** Ela não está publicada, mas fica no próprio `eboot` como **32 caracteres ASCII**, usados direto como bytes (padrão de `FAES::DecryptData` no UE4 4.17–4.20). Para achá-la, teste como chave cada janela de 32 caracteres imprimíveis do módulo `executable`, em qualquer alinhamento. A chave certa decifra o início do índice como um `FString` com `../../../`. Foram 102.619 testes em 22 s. A chave fica só no PC, fora do repositório.
2. **Índice do pak.** O rodapé ocupa os últimos 45 bytes: flag de criptografia (`u8`), magic `0x5A6F12E1`, versão (`u32`), offset do índice (`u64`), tamanho do índice (`u64`) e SHA1. O índice decifrado tem o mount point (`../../../`), o número de entradas e, para cada entrada, `FString` com o nome, `offset`, `size`, `usize`, `method` (`i32`), SHA1, os blocos (se comprimido), `bEncrypted` (`u8`) e `block_size` (`u32`). O `pakchunk0-ps4.pak` tem 14.071 arquivos, entre eles 314 `.locres`.
3. **Dados.** Em `offset`, o `FPakEntry` se repete (53 bytes, mais `4+16*n` se houver blocos comprimidos). Depois vêm os dados, com tamanho arredondado para 16 se `bEncrypted`. Dá para ler só o trecho necessário pelo FTP com `REST`, sem copiar o pak de ~1 GB.

## Estrutura do arquivo

- **Cabeçalho:** magic de 16 bytes `0E 14 74 75 67 4A 03 FC 4A 15 90 9D C3 37 7F 1B`, versão `u8` = `1` (formato "Compact") e `i64` com o offset do array de strings.
- **Tabela de chaves:** `u32` com o número de namespaces. Cada namespace tem um `FString`, um `u32` com o número de chaves e, por chave, `FString` com a chave, `u32` com o hash do texto fonte e `i32` com o índice no array de strings. Várias chaves podem apontar para a mesma string (555 chaves para 522 strings no `kh3_menu`).
- **Array de strings:** `i32` com a quantidade e depois os `FString`. No `FString`, o tamanho vem com sinal: negativo é UTF-16LE com o `\0` incluído no tamanho; positivo é ANSI.
- **Codificação do texto:** UTF-16LE. No `kh3_menu.locres` em inglês, só 3 strings têm caracteres fora do ASCII.
- **Códigos de controle** (marcação do próprio KH3, não do UE4):
  - `<br>`: quebra de linha;
  - `<color(strong)>...<color>`: destaque;
  - `<icon(Decide)>`, `<icon(cancel)>`, `<icon(Square)>`, `<icon(Triangle)>`, `<icon(KeyLeftRight)>`: ícones de botão;
  - `<i(on)>...<i(off)>`: provavelmente itálico.
- Exemplo: namespace `kh3_menu.savepoint`, chave `dialog_0007`, hash `0x1b7a3a87`, índice 70 → `<color(strong)>Tutorials<color> unlocked.<br>Tutorials allow you to go hands-on with<br>the game...`

## Em memória

- **Região típica:** heap `0x1008000000–0x100a000000`. Os textos ficam em sequência, em UTF-16LE com a marcação intacta, cada um alinhado a 8 bytes e completado com zeros. Isso bate com o array de strings do `.locres` carregado.
- **Como buscar:** dos 17,5 GiB graváveis do processo, só ~740 MiB têm dados. Leia uma amostra de 4 KiB por MiB para descartar os trechos zerados (~3 min) e depois varra só esses ~740 MiB (~1 min a 10 MiB/s pelo cabo).
- **Transformação arquivo → memória:** o texto e as tags aparecem idênticos; não vimos conversão. Ainda falta conferir com `compare_bytes`.
- **Quem escreve o buffer (`set_watchpoint`):** ainda não investigado.

## Fonte e glifos

- Arquivo da fonte:
- Glifos de ç, ã, é, ê, ó, õ presentes?
- Rotina de renderização (`module_offset` / função no ReVa):

## Limites

- Tamanho máximo observado:
- O que acontece ao passar do limite:
