/* UTF-8/UTF-16 sem dependências (núcleo do CCPS4 Agent Viewer). */
#ifndef CCPS4_UTF8_H
#define CCPS4_UTF8_H

#include <stddef.h>
#include <stdint.h>

#define UTF8_REPLACEMENT 0xFFFDu

/* Decodifica o code point que começa em s[*pos].
 * Retorna 1 se a sequência é válida, 0 se é inválida (cp = U+FFFD e avança só
 * 1 byte, para ressincronizar) e -1 no fim do texto.
 * Rejeita: overlong, surrogates (U+D800..U+DFFF), > U+10FFFF, sequência truncada
 * e byte de continuação fora de lugar. */
int utf8_next(const char *s, size_t len, size_t *pos, uint32_t *cp);

/* Codifica cp (<= U+10FFFF, sem surrogates) em UTF-8. Retorna 1..4, ou 0 se inválido. */
size_t utf8_encode(uint32_t cp, char out[4]);

/* Número de "colunas": code points válidos + 1 por byte inválido. */
size_t utf8_count(const char *s, size_t len);

/* 1 se o texto inteiro é UTF-8 válido. */
int utf8_valid(const char *s, size_t len);

/* Maior prefixo de no máximo max_bytes que não corta uma sequência multibyte. */
size_t utf8_truncate(const char *s, size_t len, size_t max_bytes);

/* UTF-16 (sem BOM) -> UTF-8 terminado em NUL. Para no primeiro 0 ou após n
 * unidades. Surrogate solto vira '?'. Nunca corta um caractere: se não couber,
 * para antes. Retorna o número de bytes escritos (sem o NUL). */
size_t utf16_to_utf8(const uint16_t *in, size_t n, char *out, size_t cap);

/* UTF-8 -> UTF-16 terminado em 0. Sequências inválidas viram '?'. Retorna o
 * número de unidades escritas (sem o 0). cap_units inclui o terminador. */
size_t utf8_to_utf16(const char *in, size_t len, uint16_t *out, size_t cap_units);

#endif
