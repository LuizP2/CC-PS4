/* Desenho em memória (ARGB8888, 0xAARRGGBB) e a tela do viewer. */
#ifndef CCPS4_RENDER_H
#define CCPS4_RENDER_H

#include <stddef.h>
#include <stdint.h>

struct viewer;

typedef struct {
    uint32_t *px;
    int w, h;
    int stride; /* em pixels */
} canvas_t;

void canvas_fill(canvas_t *c, uint32_t argb);
void canvas_rect(canvas_t *c, int x, int y, int w, int h, uint32_t argb);
void canvas_frame(canvas_t *c, int x, int y, int w, int h, int t, uint32_t argb);
/* Desenha um code point; retorna a largura avançada (8*scale). */
int canvas_glyph(canvas_t *c, int x, int y, uint32_t cp, int scale, uint32_t fg);
/* Desenha UTF-8 (inválido vira '?'); retorna o x final. max_cols < 0 = sem limite;
 * se o texto passar de max_cols, termina com '…'. */
int canvas_text(canvas_t *c, int x, int y, const char *s, size_t len, int scale, uint32_t fg, int max_cols);

/* Desenha a tela inteira do viewer e atualiza o layout guardado em v
 * (feed_cols, feed_rows, feed_total_lines, scroll limitado). */
void ui_render(canvas_t *c, struct viewer *v, uint64_t now_ms);

/* "HH:MM:SS" do timestamp unix com o fuso em minutos. */
void ui_format_hms(int64_t unix_ts, int utc_offset_min, char out[9]);

#endif
