/*
 * Tabelas de glifos. Os arquivos font8x8_basic.h e font8x8_ext_latin.h são
 * cópias sem alteração de https://github.com/dhepper/font8x8 (Public Domain,
 * Daniel Hepper; baseados nas fontes VGA de domínio público da IBM e no
 * font8_8.asm de Marcel Sondaar). O aviso de domínio público está no topo de
 * cada arquivo. Os glifos em ui_glyphs abaixo são próprios deste app.
 */
#include "font.h"

#include <stddef.h>

/* As tabelas originais são "char" com valores > 0x7F; silencia o aviso de
 * conversão sem tocar nos arquivos de terceiros. */
#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wconstant-conversion"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Woverflow"
#endif
#include "font8x8_basic.h"
#include "font8x8_ext_latin.h"
#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

enum {
    UI_CROSS, UI_CIRCLE, UI_TRIANGLE, UI_SQUARE,
    UI_UP, UI_DOWN, UI_LEFT, UI_RIGHT,
    UI_DOT, UI_ELLIPSIS, UI_TRI_LEFT, UI_TRI_RIGHT, UI_CHECK,
    UI_COUNT
};

static const uint8_t ui_glyphs[UI_COUNT][8] = {
    [UI_CROSS]     = {0xC3, 0x66, 0x3C, 0x18, 0x3C, 0x66, 0xC3, 0x00},
    [UI_CIRCLE]    = {0x3C, 0x66, 0xC3, 0xC3, 0xC3, 0x66, 0x3C, 0x00},
    [UI_TRIANGLE]  = {0x18, 0x18, 0x3C, 0x24, 0x66, 0x42, 0xFF, 0x00},
    [UI_SQUARE]    = {0x7F, 0x41, 0x41, 0x41, 0x41, 0x41, 0x7F, 0x00},
    [UI_UP]        = {0x18, 0x3C, 0x7E, 0x18, 0x18, 0x18, 0x18, 0x00},
    [UI_DOWN]      = {0x18, 0x18, 0x18, 0x18, 0x7E, 0x3C, 0x18, 0x00},
    [UI_LEFT]      = {0x08, 0x0C, 0xFE, 0xFF, 0xFE, 0x0C, 0x08, 0x00},
    [UI_RIGHT]     = {0x10, 0x30, 0x7F, 0xFF, 0x7F, 0x30, 0x10, 0x00},
    [UI_DOT]       = {0x00, 0x3C, 0x7E, 0x7E, 0x7E, 0x7E, 0x3C, 0x00},
    [UI_ELLIPSIS]  = {0x00, 0x00, 0x00, 0x00, 0x00, 0xDB, 0xDB, 0x00},
    [UI_TRI_LEFT]  = {0x60, 0x78, 0x7E, 0x7F, 0x7E, 0x78, 0x60, 0x00},
    [UI_TRI_RIGHT] = {0x06, 0x1E, 0x7E, 0xFE, 0x7E, 0x1E, 0x06, 0x00},
    [UI_CHECK]     = {0x00, 0xC0, 0x60, 0x31, 0x1B, 0x0E, 0x04, 0x00},
};

static const uint8_t *basic(unsigned c)
{
    return (const uint8_t *)font8x8_basic[c];
}

const uint8_t *font_glyph(uint32_t cp, int *fallback)
{
    int ui = -1;

    if (fallback)
        *fallback = 0;
    if (cp >= 0x20 && cp <= 0x7E)
        return basic(cp);
    if (cp >= 0xA0 && cp <= 0xFF)
        return (const uint8_t *)font8x8_ext_latin[cp - 0xA0];

    switch (cp) {
    /* símbolos de UI */
    case 0x2715: case 0x2716: case 0x2717: case 0x2718: ui = UI_CROSS; break;
    case 0x25CB: case 0x25EF:                          ui = UI_CIRCLE; break;
    case 0x25B2: case 0x25B3:                          ui = UI_TRIANGLE; break;
    case 0x25A1: case 0x25A0:                          ui = UI_SQUARE; break;
    case 0x2191: ui = UI_UP; break;
    case 0x2193: ui = UI_DOWN; break;
    case 0x2190: ui = UI_LEFT; break;
    case 0x2192: ui = UI_RIGHT; break;
    case 0x25CF: case 0x2022: case 0x2B24:             ui = UI_DOT; break;
    case 0x2026: ui = UI_ELLIPSIS; break;
    case 0x25C0: case 0x25C2:                          ui = UI_TRI_LEFT; break;
    case 0x25B6: case 0x25B8:                          ui = UI_TRI_RIGHT; break;
    case 0x2713: case 0x2714:                          ui = UI_CHECK; break;
    /* pontuação tipográfica -> equivalente ASCII */
    case 0x2018: case 0x2019: case 0x201A: case 0x2032: return basic('\'');
    case 0x201C: case 0x201D: case 0x201E: case 0x2033: return basic('"');
    case 0x2010: case 0x2011: case 0x2012: case 0x2013:
    case 0x2014: case 0x2015: case 0x2212:              return basic('-');
    case 0x2009: case 0x200A: case 0x202F: case 0x2002:
    case 0x2003:                                        return basic(' ');
    default: break;
    }
    if (ui >= 0)
        return ui_glyphs[ui];
    if (fallback)
        *fallback = 1;
    return basic('?');
}
