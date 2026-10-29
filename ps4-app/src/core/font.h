/* Fonte bitmap 8x8 (font8x8 de Daniel Hepper, domínio público) + alguns
 * glifos próprios do app (botões do controle, setas, reticências). */
#ifndef CCPS4_FONT_H
#define CCPS4_FONT_H

#include <stdint.h>

#define FONT_W 8
#define FONT_H 8

/* Retorna os 8 bytes do glifo (linha a linha; bit 0 = pixel mais à esquerda).
 * Cobertura: U+0020..U+007E (font8x8_basic), U+00A0..U+00FF (font8x8_ext_latin),
 * símbolos de UI (✕ ○ △ □ ← ↑ → ↓ ◀ ▶ ● … ✓) e pontuação tipográfica comum
 * (“ ” ‘ ’ – — •) mapeada para equivalentes. Qualquer outro code point vira '?'
 * e *fallback (se não for NULL) recebe 1. */
const uint8_t *font_glyph(uint32_t cp, int *fallback);

#endif
