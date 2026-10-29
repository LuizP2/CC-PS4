# KH3: formato das legendas (piloto da seção 11)

> Documento a ser preenchido pelo agente durante o piloto. Use só uma cópia própria e legítima do jogo, e distribua apenas o patch, nunca arquivos do jogo. A hipótese do Unreal Engine 4 com textos em `.pak` ainda não foi confirmada.

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
| Arquivo de origem (no PC) | |
| Caminho no console (`list_dir`) | |
| Contêiner (pak/locres/outro) | |
| Verificação de integridade? | |

## Estrutura do arquivo

- Cabeçalho:
- Tabela de offsets / índices:
- Codificação do texto:
- Códigos de controle (quebra de linha, cor, nome do falante, pausa):

## Em memória

- Endereço/região típica (`search_memory`):
- Transformação arquivo → memória (`compare_bytes`):
- Quem escreve o buffer (`set_watchpoint`):

## Fonte e glifos

- Arquivo da fonte:
- Glifos de ç, ã, é, ê, ó, õ presentes?
- Rotina de renderização (`module_offset` / função no ReVa):

## Limites

- Tamanho máximo observado:
- O que acontece ao passar do limite:
