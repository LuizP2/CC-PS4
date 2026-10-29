#include "render.h"

#include <stdio.h>
#include <string.h>

#include "bridge.h"
#include "font.h"
#include "utf8.h"
#include "viewer.h"
#include "wrap.h"

#define COL_BG      0xFF0D1117u
#define COL_BAR     0xFF161B22u
#define COL_LINE    0xFF30363Du
#define COL_TEXT    0xFFE6EDF3u
#define COL_DIM     0xFF8B949Eu
#define COL_ACCENT  0xFF58A6FFu
#define COL_OK      0xFF3FB950u
#define COL_WARN    0xFFD29922u
#define COL_ERR     0xFFF85149u
#define COL_SELECT  0xFF1F6FEBu
#define COL_TOAST   0xFF1C2128u
#define COL_LOCAL   0xFF6E7681u

#define MARGIN      24
#define PREFIX_COLS 16 /* "HH:MM:SS " (9) + tipo (7) */
#define MAX_WRAP    24

static const uint32_t kind_label_color[KIND_COUNT] = {
    [KIND_TOOL] = 0xFF4FC3F7u, [KIND_RESULT] = 0xFFA5D6A7u, [KIND_EVENT] = 0xFFFFB74Du,
    [KIND_STATUS] = 0xFFB39DDBu, [KIND_USER] = 0xFF7EE787u, [KIND_INFO] = 0xFF8B949Eu,
    [KIND_ERROR] = 0xFFFF7B72u,
};
static const uint32_t kind_text_color[KIND_COUNT] = {
    [KIND_TOOL] = 0xFFB3E5FCu, [KIND_RESULT] = 0xFFC9D1D9u, [KIND_EVENT] = 0xFFFFCC80u,
    [KIND_STATUS] = 0xFFD1C4E9u, [KIND_USER] = 0xFFAFF5B4u, [KIND_INFO] = 0xFFADBAC7u,
    [KIND_ERROR] = 0xFFFFA198u,
};

/* ------------------------------------------------------------ primitivas */

void canvas_rect(canvas_t *c, int x, int y, int w, int h, uint32_t argb)
{
    int x0 = x < 0 ? 0 : x, y0 = y < 0 ? 0 : y;
    int x1 = x + w > c->w ? c->w : x + w, y1 = y + h > c->h ? c->h : y + h;
    for (int yy = y0; yy < y1; yy++) {
        uint32_t *row = c->px + (size_t)yy * (size_t)c->stride;
        for (int xx = x0; xx < x1; xx++)
            row[xx] = argb;
    }
}

void canvas_fill(canvas_t *c, uint32_t argb)
{
    canvas_rect(c, 0, 0, c->w, c->h, argb);
}

void canvas_frame(canvas_t *c, int x, int y, int w, int h, int t, uint32_t argb)
{
    canvas_rect(c, x, y, w, t, argb);
    canvas_rect(c, x, y + h - t, w, t, argb);
    canvas_rect(c, x, y, t, h, argb);
    canvas_rect(c, x + w - t, y, t, h, argb);
}

int canvas_glyph(canvas_t *c, int x, int y, uint32_t cp, int scale, uint32_t fg)
{
    const uint8_t *g = font_glyph(cp, NULL);
    for (int r = 0; r < FONT_H; r++) {
        uint8_t bits = g[r];
        for (int b = 0; bits; b++, bits >>= 1)
            if (bits & 1)
                canvas_rect(c, x + b * scale, y + r * scale, scale, scale, fg);
    }
    return FONT_W * scale;
}

int canvas_text(canvas_t *c, int x, int y, const char *s, size_t len, int scale, uint32_t fg, int max_cols)
{
    size_t pos = 0;
    uint32_t cp;
    int r, cols = 0;
    int limit = max_cols;

    if (max_cols >= 0 && (int)utf8_count(s, len) > max_cols)
        limit = max_cols - 1; /* reserva 1 coluna para '…' */
    while ((r = utf8_next(s, len, &pos, &cp)) >= 0) {
        if (limit >= 0 && cols >= limit)
            break;
        if (r == 0)
            cp = '?';
        x += canvas_glyph(c, x, y, cp, scale, fg);
        cols++;
    }
    if (max_cols > 0 && limit == max_cols - 1)
        x += canvas_glyph(c, x, y, 0x2026, scale, fg);
    return x;
}

static int text_w(const char *s, int scale)
{
    return (int)utf8_count(s, strlen(s)) * FONT_W * scale;
}

static int draw_str(canvas_t *c, int x, int y, const char *s, int scale, uint32_t fg)
{
    return canvas_text(c, x, y, s, strlen(s), scale, fg, -1);
}

void ui_format_hms(int64_t unix_ts, int utc_offset_min, char out[9])
{
    int64_t t = unix_ts + (int64_t)utc_offset_min * 60;
    int64_t sec = ((t % 86400) + 86400) % 86400;
    snprintf(out, 9, "%02d:%02d:%02d", (int)(sec / 3600), (int)(sec / 60 % 60), (int)(sec % 60));
}

/* "rótulo: valor" com o rótulo apagado; avança x e respeita max_x. */
static void chip(canvas_t *c, int *x, int y, int max_x, int s, const char *label, const char *value,
                 uint32_t color)
{
    int cw = FONT_W * s, avail;
    if (*x >= max_x)
        return;
    if (label && label[0]) {
        avail = (max_x - *x) / cw;
        *x = canvas_text(c, *x, y, label, strlen(label), s, COL_DIM, avail);
        *x += cw;
    }
    avail = (max_x - *x) / cw;
    if (avail > 0)
        *x = canvas_text(c, *x, y, value, strlen(value), s, color, avail);
    *x += 3 * cw;
}

/* ------------------------------------------------------------ barra de status */

static void draw_status(canvas_t *c, viewer_t *v, uint64_t now, int s, int lh, int top_h)
{
    int cw = FONT_W * s, y1 = 12 + s, y2 = y1 + lh, y3 = y2 + lh;
    int max_x = c->w - MARGIN, x;
    char buf[200], val[96];
    uint32_t conn_col;
    const bridge_state_t *st = &v->state;
    int online = v->conn == CONN_ONLINE;

    canvas_rect(c, 0, 0, c->w, top_h, COL_BAR);
    canvas_rect(c, 0, top_h - 2, c->w, 2, COL_LINE);

    x = draw_str(c, MARGIN, y1, "CCPS4 Agent Viewer", s, COL_ACCENT);
    draw_str(c, MARGIN + 1, y1, "CCPS4 Agent Viewer", s, COL_ACCENT); /* "negrito" */
    (void)x;

    switch (v->conn) {
    case CONN_DISCOVERING:
        conn_col = COL_WARN;
        if (v->last_error[0])
            snprintf(buf, sizeof(buf), "procurando o PC... (%s)", v->last_error);
        else
            snprintf(buf, sizeof(buf), "procurando o PC (anúncio UDP %u)...", (unsigned)v->cfg.udp_port);
        break;
    case CONN_CONNECTING:
        conn_col = COL_WARN;
        snprintf(buf, sizeof(buf), "conectando a %s...", v->pc_label);
        break;
    case CONN_ONLINE:
        conn_col = COL_OK;
        snprintf(buf, sizeof(buf), "conectado · PC %s", v->pc_label);
        break;
    default: {
        long left = v->retry_at > now ? (long)((v->retry_at - now + 999) / 1000) : 0;
        conn_col = COL_ERR;
        snprintf(buf, sizeof(buf), "desconectado · nova tentativa em %ld s", left);
        break;
    }
    }
    {
        int cols = (int)utf8_count(buf, strlen(buf));
        int room = (max_x - (MARGIN + 20 * cw)) / cw - 2;
        if (cols > room)
            cols = room;
        x = max_x - (cols + 2) * cw;
        canvas_glyph(c, x, y1, 0x25CF, s, conn_col);
        canvas_text(c, x + 2 * cw, y1, buf, strlen(buf), s, conn_col, cols);
    }

    if (!v->have_state) {
        draw_str(c, MARGIN, y2, "estado do agente: ainda não recebido", s, COL_DIM);
    } else {
        uint32_t dimmed = online ? 0 : COL_DIM; /* fora do ar: último estado, apagado */
        const char *agent = st->agent == AGENT_IDLE      ? "ocioso"
                            : st->agent == AGENT_WORKING ? "trabalhando"
                            : st->agent == AGENT_WAITING_EVENT ? "esperando evento"
                                                               : "?";
        uint32_t acol = st->agent == AGENT_WORKING ? COL_ACCENT
                        : st->agent == AGENT_WAITING_EVENT ? COL_WARN : COL_TEXT;
        x = MARGIN;
        chip(c, &x, y2, max_x, s, "agente:", agent, dimmed ? dimmed : acol);
        chip(c, &x, y2, max_x, s, "ps4debug:",
             st->ps4debug == 1 ? "conectado" : st->ps4debug == 0 ? "desconectado" : "?",
             dimmed ? dimmed : (st->ps4debug == 1 ? COL_OK : COL_ERR));
        if (st->attached_pid >= 0)
            snprintf(val, sizeof(val), "pid %ld%s%s", st->attached_pid, st->process[0] ? " · " : "",
                     st->process);
        else
            snprintf(val, sizeof(val), "%s", st->process[0] ? st->process : "nenhum anexado");
        chip(c, &x, y2, max_x, s, "processo:", val, dimmed ? dimmed : COL_TEXT);

        x = MARGIN;
        if (v->conn == CONN_OFFLINE && v->last_error[0]) {
            chip(c, &x, y3, max_x, s, "erro:", v->last_error, COL_ERR);
        } else {
            if (st->paused == 1) {
                int w = text_w(" PAUSADO ", s);
                canvas_rect(c, x - s, y3 - s, w + 2 * s, lh, dimmed ? COL_LINE : COL_ERR);
                draw_str(c, x, y3, " PAUSADO ", s, 0xFFFFFFFFu);
                x += w + 3 * cw;
            } else {
                chip(c, &x, y3, max_x, s, "", st->paused == 0 ? "rodando" : "pausa: ?", dimmed ? dimmed : COL_OK);
            }
            snprintf(val, sizeof(val), "%ld", st->pending_events < 0 ? 0 : st->pending_events);
            chip(c, &x, y3, max_x, s, "eventos:", st->pending_events < 0 ? "?" : val,
                 dimmed ? dimmed : (st->pending_events > 0 ? COL_WARN : COL_TEXT));
            snprintf(val, sizeof(val), "%ld", st->messages_pending < 0 ? 0 : st->messages_pending);
            chip(c, &x, y3, max_x, s, "msgs na fila:", st->messages_pending < 0 ? "?" : val,
                 dimmed ? dimmed : COL_TEXT);
            chip(c, &x, y3, max_x, s, "última:", st->last_tool[0] ? st->last_tool : "-",
                 dimmed ? dimmed : COL_ACCENT);
        }
    }
}

/* ------------------------------------------------------------ feed */

static void draw_feed_line(canvas_t *c, viewer_t *v, const feed_entry_t *e, const wrap_span_t *sp,
                           int first, int x0, int y, int s)
{
    int cw = FONT_W * s;
    int kind = (e->kind >= 0 && e->kind < KIND_COUNT) ? e->kind : KIND_INFO;
    if (first) {
        char hms[9];
        const char *kn = bridge_kind_name(kind);
        ui_format_hms(e->ts, v->cfg.utc_offset_min, hms);
        draw_str(c, x0, y, hms, s, e->seq ? COL_DIM : COL_LOCAL);
        draw_str(c, x0 + 9 * cw, y, kn, s, kind_label_color[kind]);
    }
    canvas_text(c, x0 + PREFIX_COLS * cw, y, e->text + sp->start, sp->end - sp->start, s,
                kind_text_color[kind], -1);
}

static void draw_feed(canvas_t *c, viewer_t *v, int s, int lh, int y_top, int y_bot)
{
    int cw = FONT_W * s;
    int cols = (c->w - 2 * MARGIN - 2 * cw) / cw;
    int tcols = cols - PREFIX_COLS < 10 ? 10 : cols - PREFIX_COLS;
    int rows = (y_bot - y_top) / lh;
    int total = 0, line = 0, max_scroll;
    wrap_span_t sp[MAX_WRAP];

    v->feed_cols = tcols;
    v->feed_rows = rows;

    for (int i = 0; i < v->feed.count; i++) {
        const feed_entry_t *e = viewer_feed_at(v, i);
        int n = wrap_utf8(e->text, e->text_len, tcols, NULL, 0);
        total += n > MAX_WRAP ? MAX_WRAP : n;
    }
    v->feed_total_lines = total;
    max_scroll = total - rows > 0 ? total - rows : 0;
    if (v->scroll > max_scroll)
        v->scroll = max_scroll;
    if (v->scroll <= 0) {
        v->scroll = 0;
        v->unseen = 0;
    }

    if (v->feed.count == 0) {
        draw_str(c, MARGIN, y_top + lh, "Sem atividade ainda.", s, COL_DIM);
        return;
    }

    for (int i = v->feed.count - 1; i >= 0 && line < v->scroll + rows; i--) {
        const feed_entry_t *e = viewer_feed_at(v, i);
        int n = wrap_utf8(e->text, e->text_len, tcols, sp, MAX_WRAP);
        if (n > MAX_WRAP)
            n = MAX_WRAP;
        for (int j = n - 1; j >= 0; j--, line++) {
            if (line < v->scroll || line >= v->scroll + rows)
                continue;
            int y = y_bot - (line - v->scroll + 1) * lh + s;
            draw_feed_line(c, v, e, &sp[j], j == 0, MARGIN, y, s);
        }
    }

    /* barra de rolagem */
    if (total > rows) {
        int track_x = c->w - MARGIN - s * 2, track_h = y_bot - y_top;
        int thumb_h = track_h * rows / total;
        int thumb_y;
        if (thumb_h < 3 * s)
            thumb_h = 3 * s;
        thumb_y = y_bot - thumb_h - (track_h - thumb_h) * v->scroll / (max_scroll ? max_scroll : 1);
        canvas_rect(c, track_x, y_top, s * 2, track_h, COL_BAR);
        canvas_rect(c, track_x, thumb_y, s * 2, thumb_h, v->scroll ? COL_WARN : COL_LINE);
    }
    if (v->scroll > 0) {
        char buf[96];
        int y = y_bot - lh;
        snprintf(buf, sizeof(buf), "↓ %d linha(s) nova(s) abaixo · ○ volta ao fim", v->unseen);
        canvas_rect(c, MARGIN - 8, y - 2, c->w - 2 * MARGIN + 16 - 4 * s, lh + 2, COL_BAR);
        draw_str(c, MARGIN, y + s, v->unseen ? buf : "↑ rolado para cima · ○ volta ao fim", s, COL_WARN);
    }
}

/* ------------------------------------------------------------ rodapé */

static void draw_phrase_bar(canvas_t *c, viewer_t *v, int s, int lh, int y, int h)
{
    int cw = FONT_W * s, x = MARGIN, ty = y + (h - FONT_H * s) / 2;
    char idx[32];
    const char *p = viewer_phrases[v->phrase];
    int pw = text_w(p, s);

    (void)lh;
    canvas_rect(c, 0, y, c->w, h, COL_BAR);
    canvas_rect(c, 0, y, c->w, 2, COL_LINE);
    snprintf(idx, sizeof(idx), "frase %d/%d", v->phrase + 1, viewer_phrase_count);
    x = draw_str(c, x, ty, idx, s, COL_DIM) + cw;
    x += canvas_glyph(c, x, ty, 0x25C0, s, COL_ACCENT) + cw;
    canvas_rect(c, x - cw / 2, y + 6, pw + cw, h - 12, COL_SELECT);
    x = draw_str(c, x, ty, p, s, 0xFFFFFFFFu) + cw;
    x += canvas_glyph(c, x, ty, 0x25B6, s, COL_ACCENT) + 3 * cw;
    x += canvas_glyph(c, x, ty, 0x2715, s, COL_ACCENT) + cw;
    x = draw_str(c, x, ty, "envia", s, COL_TEXT) + 3 * cw;
    x += canvas_glyph(c, x, ty, 0x25B3, s, COL_OK) + cw;
    draw_str(c, x, ty, "digitar", s, COL_TEXT);
}

static void draw_help_bar(canvas_t *c, int s, int y, int h)
{
    static const struct { const char *sym, *label; } items[] = {
        {"↑↓", "rolar"}, {"L1/R1", "página"}, {"○", "ir ao fim"},
        {"←→", "escolher frase"}, {"✕", "enviar"}, {"△", "teclado"},
    };
    int n = (int)(sizeof(items) / sizeof(items[0]));
    int hs = s, total, x, ty;

    for (;;) { /* usa a escala da tela se couber; senão uma menor */
        total = 0;
        for (int i = 0; i < n; i++)
            total += text_w(items[i].sym, hs) + text_w(items[i].label, hs) + 4 * FONT_W * hs;
        if (total <= c->w - 2 * MARGIN || hs <= 1)
            break;
        hs--;
    }
    canvas_rect(c, 0, y, c->w, h, COL_BG);
    canvas_rect(c, 0, y, c->w, 2, COL_LINE);
    x = (c->w - total) / 2;
    ty = y + (h - FONT_H * hs) / 2;
    for (int i = 0; i < n; i++) {
        x = draw_str(c, x, ty, items[i].sym, hs, COL_ACCENT) + FONT_W * hs;
        x = draw_str(c, x, ty, items[i].label, hs, COL_DIM) + 3 * FONT_W * hs;
    }
}

static void draw_toast(canvas_t *c, viewer_t *v, int s, int lh, int y_bottom)
{
    int cw = FONT_W * s, max_cols = (c->w - 2 * MARGIN - 4 * cw) / cw;
    wrap_span_t sp[3];
    size_t len = strlen(v->toast);
    int n = wrap_utf8(v->toast, len, max_cols, sp, 3), w = 0, h, x, y;
    uint32_t border = v->toast_kind == TOAST_OK ? COL_OK : v->toast_kind == TOAST_ERROR ? COL_ERR : COL_ACCENT;

    if (n > 3)
        n = 3;
    for (int i = 0; i < n; i++) {
        int cols = (int)utf8_count(v->toast + sp[i].start, sp[i].end - sp[i].start);
        if (cols * cw > w)
            w = cols * cw;
    }
    w += 4 * cw;
    h = n * lh + 2 * s + 24;
    x = (c->w - w) / 2;
    y = y_bottom - h - 12;
    canvas_rect(c, x, y, w, h, COL_TOAST);
    canvas_frame(c, x, y, w, h, 4, border);
    for (int i = 0; i < n; i++)
        canvas_text(c, x + 2 * cw, y + 12 + s + i * lh, v->toast + sp[i].start, sp[i].end - sp[i].start, s,
                    COL_TEXT, -1);
}

void ui_render(canvas_t *c, viewer_t *v, uint64_t now_ms)
{
    int s = v->cfg.font_scale == 2 ? 2 : 3;
    int lh = 10 * s;
    int top_h = 12 + 3 * lh + 8;
    int help_h = lh + 16;
    int phrase_h = lh + 24;
    int y_phrase = c->h - help_h - phrase_h;

    int overlay_h = 0;

    /* Aviso/teclado ganham uma faixa própria acima da barra de frases: o feed
     * encolhe em vez de ficar por baixo (as linhas mais novas continuam visíveis). */
    if (v->ime_active) {
        overlay_h = lh + 40;
    } else if (v->toast[0]) {
        int cols = (c->w - 2 * MARGIN - 4 * FONT_W * s) / (FONT_W * s);
        int n = wrap_utf8(v->toast, strlen(v->toast), cols, NULL, 0);
        overlay_h = (n > 3 ? 3 : n) * lh + 2 * s + 24 + 12;
    }
    canvas_fill(c, COL_BG);
    draw_status(c, v, now_ms, s, lh, top_h);
    draw_feed(c, v, s, lh, top_h + 10, y_phrase - 10 - overlay_h);
    draw_phrase_bar(c, v, s, lh, y_phrase, phrase_h);
    draw_help_bar(c, s, c->h - help_h, help_h);
    if (v->ime_active) {
        const char *m = "teclado aberto: digite a mensagem e confirme";
        int w = text_w(m, s) + 4 * FONT_W * s;
        canvas_rect(c, (c->w - w) / 2, y_phrase - lh - 40, w, lh + 24, COL_TOAST);
        canvas_frame(c, (c->w - w) / 2, y_phrase - lh - 40, w, lh + 24, 4, COL_ACCENT);
        draw_str(c, (c->w - w) / 2 + 2 * FONT_W * s, y_phrase - lh - 40 + 12 + s, m, s, COL_TEXT);
    } else if (v->toast[0]) {
        draw_toast(c, v, s, lh, y_phrase);
    }
}
