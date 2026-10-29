#include "viewer.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "../platform.h"
#include "utf8.h"
#include "wrap.h"

const char *const viewer_phrases[] = {
    "continue",
    "pare e me explique o que achou",
    "pode retomar o jogo (resume)",
    "sim",
    "não",
    "tira um snapshot agora",
    "resume em 3 linhas o que você sabe",
};
const int viewer_phrase_count = (int)(sizeof(viewer_phrases) / sizeof(viewer_phrases[0]));

/* ------------------------------------------------------------------ feed */

const feed_entry_t *viewer_feed_at(const viewer_t *v, int i)
{
    if (i < 0 || i >= v->feed.count)
        return NULL;
    return &v->feed.items[(v->feed.head + i) % FEED_CAP];
}

static void feed_push(viewer_t *v, const feed_entry_t *e)
{
    feed_ring_t *f = &v->feed;
    int idx;

    if (f->count == FEED_CAP) {
        f->head = (f->head + 1) % FEED_CAP; /* descarta a mais antiga */
        f->count--;
    }
    idx = (f->head + f->count) % FEED_CAP;
    f->items[idx] = *e;
    f->count++;

    /* Rolado para cima: mantém as mesmas linhas na tela e conta as novas. */
    if (v->scroll > 0 && v->feed_cols > 0) {
        int lines = wrap_utf8(e->text, e->text_len, v->feed_cols, NULL, 0);
        v->scroll += lines;
        v->unseen += lines;
    }
    v->dirty = 1;
}

static void feed_clear(viewer_t *v)
{
    v->feed.head = 0;
    v->feed.count = 0;
    v->scroll = 0;
    v->unseen = 0;
}

void viewer_add_local(viewer_t *v, int kind, const char *fmt, ...)
{
    feed_entry_t e;
    va_list ap;
    char tmp[BRIDGE_TEXT_MAX * 2];

    memset(&e, 0, sizeof(e));
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    e.seq = 0;
    e.ts = (int64_t)time(NULL);
    e.kind = kind;
    e.text_len = (uint16_t)utf8_truncate(tmp, strlen(tmp), BRIDGE_TEXT_MAX);
    memcpy(e.text, tmp, e.text_len);
    e.text[e.text_len] = '\0';
    feed_push(v, &e);
}

void viewer_toast(viewer_t *v, int kind, uint64_t now, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(v->toast, sizeof(v->toast), fmt, ap);
    va_end(ap);
    v->toast_kind = kind;
    v->toast_until = now + VIEWER_TOAST_MS;
    v->dirty = 1;
}

/* ------------------------------------------------------------------ init */

void viewer_init(viewer_t *v, const viewer_config_t *cfg, uint64_t now)
{
    memset(v, 0, sizeof(*v));
    v->cfg = *cfg;
    v->udp_fd = -1;
    httpc_init(&v->http);
    bridge_state_reset(&v->state);
    v->resync = 1;
    v->dirty = 1;
    viewer_add_local(v, KIND_INFO, "CCPS4 Agent Viewer iniciado.");

    if (cfg->host[0]) {
        if (net_parse_ipv4(cfg->host, &v->pc_ip)) {
            v->host_configured = 1;
            v->pc_port = cfg->port;
            snprintf(v->pc_label, sizeof(v->pc_label), "%s:%u", cfg->host, (unsigned)cfg->port);
            v->conn = CONN_CONNECTING;
            v->next_poll_at = now;
            viewer_add_local(v, KIND_INFO, "PC configurado em %s (viewer.cfg); descoberta UDP desligada.",
                             v->pc_label);
            return;
        }
        viewer_add_local(v, KIND_ERROR, "host=%s inválido no viewer.cfg; usando descoberta UDP.", cfg->host);
    }
    v->conn = CONN_DISCOVERING;
    viewer_add_local(v, KIND_INFO, "Procurando o PC (anúncio UDP na porta %u)...", (unsigned)cfg->udp_port);
}

void viewer_shutdown(viewer_t *v)
{
    httpc_abort(&v->http);
    if (v->udp_fd >= 0)
        net_close(v->udp_fd);
    v->udp_fd = -1;
}

/* ------------------------------------------------------------------ rede */

static void go_offline(viewer_t *v, uint64_t now, const char *why)
{
    snprintf(v->last_error, sizeof(v->last_error), "%s", why);
    if (v->conn == CONN_ONLINE || !v->was_online) {
        viewer_add_local(v, KIND_ERROR, "Conexão com o PC %s falhou: %s",
                         v->pc_label[0] ? v->pc_label : "?", why);
        v->was_online = 1; /* não repete a mesma linha a cada nova tentativa */
    }
    plat_log("[viewer] desconectado: %s", why);
    httpc_abort(&v->http);
    v->conn = CONN_OFFLINE;
    v->retry_at = now + VIEWER_RETRY_MS;
    v->req = REQ_NONE;
    v->feed_due = 0;
    v->resync = 1;
    v->dirty = 1;
}

static void fail_request(viewer_t *v, uint64_t now, const char *why)
{
    if (v->req == REQ_MESSAGE) {
        v->out_pending = 0;
        viewer_toast(v, TOAST_ERROR, now, "Não enviado: %s", why);
    }
    go_offline(v, now, why);
}

static void start_request(viewer_t *v, int kind, uint64_t now)
{
    char req[HTTPC_REQ_MAX];
    char path[64];
    int n;

    if (kind == REQ_STATE) {
        n = http_build_get(req, sizeof(req), v->pc_label, "/v1/state");
    } else if (kind == REQ_FEED) {
        snprintf(path, sizeof(path), "/v1/feed?since=%llu",
                 (unsigned long long)(v->resync ? 0 : v->max_seq));
        n = http_build_get(req, sizeof(req), v->pc_label, path);
    } else {
        n = http_build_post(req, sizeof(req), v->pc_label, "/v1/message", v->out_msg, v->out_len);
    }
    v->req = kind;
    if (n < 0) {
        fail_request(v, now, "requisição não coube no buffer");
        return;
    }
    if (httpc_start(&v->http, v->pc_ip, v->pc_port, req, (size_t)n, now, VIEWER_CONNECT_MS,
                    VIEWER_REQUEST_MS) == HTTPC_FAIL)
        fail_request(v, now, v->http.error);
}

static void mark_online(viewer_t *v, uint64_t now)
{
    v->last_ok_at = now;
    if (v->conn != CONN_ONLINE) {
        v->conn = CONN_ONLINE;
        v->was_online = 1;
        v->last_error[0] = '\0';
        viewer_add_local(v, KIND_INFO, "Conectado ao PC %s.", v->pc_label);
        plat_log("[viewer] conectado a %s", v->pc_label);
    }
}

static void feed_cb(void *ctx, const feed_entry_t *e)
{
    viewer_t *v = ctx;
    if (e->seq <= v->max_seq)
        return; /* já mostrado */
    feed_push(v, e);
    v->max_seq = e->seq;
}

static void apply_feed(viewer_t *v, const char *body, size_t len)
{
    feed_stats_t st;

    if (v->resync && v->max_seq > 0) {
        /* Após reconectar pedimos since=0. Se o maior seq do PC for menor que o
         * nosso, o bridge reiniciou (seq voltou a 1): recarrega o feed. */
        bridge_parse_feed(body, len, NULL, NULL, &st);
        if (st.accepted > 0 && st.max_seq < v->max_seq) {
            feed_clear(v);
            v->max_seq = 0;
            viewer_add_local(v, KIND_INFO, "O bridge no PC reiniciou; feed recarregado.");
        }
    }
    bridge_parse_feed(body, len, feed_cb, v, &st);
    v->resync = 0;
    if (st.malformed) {
        v->malformed_total += (unsigned)st.malformed;
        plat_log("[viewer] feed: %d linha(s) malformada(s) ignorada(s)", st.malformed);
    }
    /* Página cheia (100): ainda há mais entradas; pede de novo logo. */
    v->feed_due = st.accepted >= 100;
}

static void trim_body(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' '))
        s[--n] = '\0';
}

static void handle_response(viewer_t *v, uint64_t now)
{
    int status = v->http.resp.status;
    char *body = v->http.body;
    size_t len = v->http.resp.body_len;
    char why[96];

    if (v->req == REQ_MESSAGE) {
        long id = 0;
        v->out_pending = 0;
        if (status == 200 && bridge_parse_ok(body, len, &id)) {
            v->sent_ok++;
            if (v->out_from_ime)
                v->ime_last[0] = '\0';
            viewer_toast(v, TOAST_OK, now, "Enviado ao agente (#%ld): «%s»", id, v->out_msg);
            v->next_poll_at = now; /* traz o eco "você: ..." logo */
        } else if (status == 400) {
            trim_body(body);
            viewer_toast(v, TOAST_ERROR, now, "Recusada pelo PC (400): %s", len ? body : "mensagem inválida");
        } else if (status == 403) {
            viewer_toast(v, TOAST_ERROR, now, "Recusada pelo PC (403): este PS4 não tem permissão");
        } else {
            viewer_toast(v, TOAST_ERROR, now, "Erro HTTP %d ao enviar a mensagem", status);
        }
        return;
    }

    if (status != 200) {
        if (status == 403)
            snprintf(why, sizeof(why), "o PC recusou este PS4 (HTTP 403)");
        else
            snprintf(why, sizeof(why), "HTTP %d em %s", status, v->req == REQ_STATE ? "/v1/state" : "/v1/feed");
        go_offline(v, now, why);
        return;
    }
    if (v->req == REQ_STATE) {
        bridge_parse_state(body, len, &v->state);
        v->have_state = 1;
        mark_online(v, now);
        v->feed_due = 1;
    } else {
        apply_feed(v, body, len);
        v->polls_ok++;
        v->last_ok_at = now;
        v->next_poll_at = now + VIEWER_POLL_MS;
    }
    v->dirty = 1;
}

static void drain_udp(viewer_t *v, int use_it)
{
    char buf[256];
    uint32_t ip;
    uint16_t sport, http_port;
    long n;

    if (v->udp_fd < 0)
        return;
    for (int i = 0; i < 64; i++) {
        n = net_udp_recv(v->udp_fd, buf, sizeof(buf), &ip, &sport);
        if (n == NET_AGAIN)
            return;
        if (n < 0) {
            net_close(v->udp_fd);
            v->udp_fd = -1;
            return;
        }
        if (!use_it || !bridge_parse_discovery(buf, (size_t)n, &http_port))
            continue;
        v->pc_ip = ip;
        v->pc_port = http_port;
        net_format_ipv4(ip, v->pc_label, sizeof(v->pc_label));
        snprintf(v->pc_label + strlen(v->pc_label), sizeof(v->pc_label) - strlen(v->pc_label), ":%u",
                 (unsigned)http_port);
        v->conn = CONN_CONNECTING;
        v->next_poll_at = 0;
        v->dirty = 1;
        plat_log("[viewer] PC anunciado: %s", v->pc_label);
        use_it = 0; /* descarta o resto da fila */
    }
}

static void handle_ime(viewer_t *v, uint64_t now)
{
    char text[BRIDGE_MSG_MAX * 2];
    int r = plat_ime_poll(text, sizeof(text));
    size_t len;

    if (r == IME_RUNNING)
        return;
    v->ime_active = 0;
    v->latch = 1; /* o X que confirmou o teclado não pode virar "enviar frase" */
    v->dirty = 1;
    if (r == IME_CANCELED) {
        viewer_toast(v, TOAST_INFO, now, "Teclado fechado; nada foi enviado.");
        return;
    }
    if (r != IME_DONE) {
        viewer_toast(v, TOAST_ERROR, now, "teclado indisponível");
        return;
    }
    len = strlen(text);
    if (len > BRIDGE_MSG_MAX) {
        len = utf8_truncate(text, len, BRIDGE_MSG_MAX);
        text[len] = '\0';
    }
    memcpy(v->ime_last, text, len + 1);
    if (viewer_send(v, text, len, now) == 0)
        v->out_from_ime = 1;
}

void viewer_tick(viewer_t *v, uint64_t now)
{
    if (v->toast[0] && now >= v->toast_until) {
        v->toast[0] = '\0';
        v->dirty = 1;
    }
    if (now / 1000 != v->last_sec) { /* contadores na tela mudam a cada segundo */
        v->last_sec = now / 1000;
        v->dirty = 1;
    }
    if (v->ime_active)
        handle_ime(v, now);

    if (!v->host_configured && v->udp_fd < 0 && now >= v->udp_retry_at) {
        v->udp_fd = net_udp_bind(v->cfg.udp_port);
        if (v->udp_fd < 0) {
            v->udp_fd = -1;
            snprintf(v->last_error, sizeof(v->last_error), "UDP %u: %s", (unsigned)v->cfg.udp_port,
                     net_last_error());
            v->udp_retry_at = now + VIEWER_RETRY_MS;
        }
    }

    switch (v->conn) {
    case CONN_DISCOVERING:
        drain_udp(v, 1);
        break;
    case CONN_OFFLINE:
        drain_udp(v, 0);
        if (now >= v->retry_at) {
            v->conn = v->host_configured ? CONN_CONNECTING : CONN_DISCOVERING;
            v->next_poll_at = now;
            v->dirty = 1;
        }
        break;
    default:
        drain_udp(v, 0);
        break;
    }

    if (v->req != REQ_NONE) {
        int r = httpc_step(&v->http, now);
        if (r == HTTPC_PENDING)
            return;
        if (r == HTTPC_FAIL)
            fail_request(v, now, v->http.error);
        else
            handle_response(v, now);
        v->req = REQ_NONE;
    }

    if (v->conn != CONN_CONNECTING && v->conn != CONN_ONLINE)
        return;
    if (v->out_pending && v->conn == CONN_ONLINE)
        start_request(v, REQ_MESSAGE, now);
    else if (v->feed_due)
        start_request(v, REQ_FEED, now);
    else if (now >= v->next_poll_at)
        start_request(v, REQ_STATE, now);
}

/* ------------------------------------------------------------------ entrada */

int viewer_send(viewer_t *v, const char *utf8, size_t len, uint64_t now)
{
    const char *bad = bridge_check_message(utf8, len);

    if (bad) {
        viewer_toast(v, TOAST_ERROR, now, "Não enviado: %s", bad);
        return -1;
    }
    if (v->conn != CONN_ONLINE) {
        viewer_toast(v, TOAST_ERROR, now, "Não enviado: PC desconectado");
        return -1;
    }
    if (v->out_pending) {
        viewer_toast(v, TOAST_INFO, now, "Aguarde: a mensagem anterior ainda está sendo enviada");
        return -1;
    }
    memcpy(v->out_msg, utf8, len);
    v->out_msg[len] = '\0';
    v->out_len = len;
    v->out_pending = 1;
    v->out_from_ime = 0;
    viewer_toast(v, TOAST_INFO, now, "Enviando: «%s»...", v->out_msg);
    return 0;
}

static void scroll_by(viewer_t *v, int d)
{
    int max = v->feed_total_lines - v->feed_rows;
    if (max < 0)
        max = 0;
    v->scroll += d;
    if (v->scroll > max)
        v->scroll = max;
    if (v->scroll <= 0) {
        v->scroll = 0;
        v->unseen = 0;
    }
}

void viewer_input(viewer_t *v, uint32_t buttons, uint64_t now)
{
    uint32_t pressed, act, rep = 0;
    int page = v->feed_rows > 2 ? v->feed_rows - 2 : 1;

    if (v->ime_active) {
        v->held = buttons;
        return;
    }
    if (v->latch) {
        if (buttons == 0)
            v->latch = 0;
        v->held = buttons;
        return;
    }
    pressed = buttons & ~v->held;
    if (buttons & (VB_UP | VB_DOWN)) {
        if (pressed & (VB_UP | VB_DOWN)) {
            v->repeat_at = now + 400;
        } else if (now >= v->repeat_at) {
            rep = buttons & (VB_UP | VB_DOWN);
            v->repeat_at = now + 60;
        }
    }
    v->held = buttons;
    act = pressed | rep;
    if (!act)
        return;
    v->dirty = 1;

    if (act & VB_UP)
        scroll_by(v, 1);
    if (act & VB_DOWN)
        scroll_by(v, -1);
    if (pressed & VB_L1)
        scroll_by(v, page);
    if (pressed & VB_R1)
        scroll_by(v, -page);
    if (pressed & VB_CIRCLE)
        scroll_by(v, -v->scroll);
    if (pressed & VB_LEFT)
        v->phrase = (v->phrase + viewer_phrase_count - 1) % viewer_phrase_count;
    if (pressed & VB_RIGHT)
        v->phrase = (v->phrase + 1) % viewer_phrase_count;
    if (pressed & VB_CROSS) {
        const char *p = viewer_phrases[v->phrase];
        viewer_send(v, p, strlen(p), now);
    }
    if (pressed & VB_TRIANGLE) {
        if (plat_ime_open("Mensagem para o agente no PC", v->ime_last[0] ? v->ime_last : NULL,
                          VIEWER_IME_CHARS) == 0) {
            v->ime_active = 1;
        } else {
            viewer_toast(v, TOAST_ERROR, now, "teclado indisponível");
        }
    }
}
