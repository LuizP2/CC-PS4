/* Quebra de linha por palavras (largura em colunas = code points; fonte monoespaçada). */
#ifndef CCPS4_WRAP_H
#define CCPS4_WRAP_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t start; /* offset em bytes (inclusive) */
    uint32_t end;   /* offset em bytes (exclusive) */
} wrap_span_t;

/* Quebra s[0..len) em linhas de no máximo `cols` colunas.
 * - quebra preferencialmente em espaço (o espaço some no ponto de quebra);
 * - palavra maior que a linha é cortada à força;
 * - '\n' força quebra; '\t' conta como espaço;
 * - espaços no começo de linha de continuação são pulados.
 * Escreve até max_out spans e retorna o total de linhas (pode ser > max_out).
 * Texto vazio produz 1 linha vazia. cols < 1 é tratado como 1. */
int wrap_utf8(const char *s, size_t len, int cols, wrap_span_t *out, int max_out);

#endif
