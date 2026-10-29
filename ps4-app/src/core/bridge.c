#include "bridge.h"

#include <string.h>

#include "utf8.h"

static const char *const kind_names[KIND_COUNT] = {
    "tool", "result", "event", "status", "user", "info", "error",
};

const char *bridge_kind_name(int kind)
{
    return (kind >= 0 && kind < KIND_COUNT) ? kind_names[kind] : "info";
}

int bridge_kind_parse(const char *s, size_t n)
{
    for (int k = 0; k < KIND_COUNT; k++)
        if (strlen(kind_names[k]) == n && memcmp(kind_names[k], s, n) == 0)
            return k;
    return -1;
}

/* Inteiro sem sinal só com dígitos (1..19 dígitos). */
static int parse_u64(const char *s, size_t n, uint64_t *out)
{
    uint64_t v = 0;
    if (n == 0 || n > 19)
        return 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] < '0' || s[i] > '9')
            return 0;
        v = v * 10 + (uint64_t)(s[i] - '0');
    }
    *out = v;
    return 1;
}

/* Inteiro com sinal opcional; aceita fração ".ddd" (descartada). */
static int parse_i64(const char *s, size_t n, int64_t *out)
{
    int neg = 0;
    uint64_t v;
    const char *dot;

    if (n > 0 && (s[0] == '-' || s[0] == '+')) {
        neg = s[0] == '-';
        s++;
        n--;
    }
    dot = memchr(s, '.', n);
    if (dot) {
        size_t frac = n - (size_t)(dot - s) - 1;
        for (size_t i = 0; i < frac; i++)
            if (dot[1 + i] < '0' || dot[1 + i] > '9')
                return 0;
        n = (size_t)(dot - s);
    }
    if (!parse_u64(s, n, &v) || v > (uint64_t)INT64_MAX)
        return 0;
    *out = neg ? -(int64_t)v : (int64_t)v;
    return 1;
}

static void trim(const char **a, const char **b)
{
    while (*a < *b && (**a == ' ' || **a == '\t' || **a == '\r'))
        (*a)++;
    while (*b > *a && ((*b)[-1] == ' ' || (*b)[-1] == '\t' || (*b)[-1] == '\r'))
        (*b)--;
}

static int key_is(const char *k, size_t n, const char *want)
{
    return strlen(want) == n && memcmp(k, want, n) == 0;
}

/* Itera linhas "chave=valor": chama fn para cada uma (já sem espaços nas pontas). */
typedef int (*kv_fn)(void *ctx, const char *k, size_t kn, const char *v, size_t vn);

static void for_each_kv(const char *text, size_t len, kv_fn fn, void *ctx, int *bad)
{
    const char *p = text, *end = text + len;
    while (p < end) {
        const char *nl = memchr(p, '\n', (size_t)(end - p));
        const char *le = nl ? nl : end, *ka = p, *eq, *ke, *va, *ve;
        p = nl ? nl + 1 : end;
        trim(&ka, &le);
        if (ka == le || *ka == '#' || *ka == ';')
            continue;
        eq = memchr(ka, '=', (size_t)(le - ka));
        if (!eq) {
            if (bad)
                (*bad)++;
            continue;
        }
        ke = eq;
        va = eq + 1;
        ve = le;
        trim(&ka, &ke);
        trim(&va, &ve);
        if (!fn(ctx, ka, (size_t)(ke - ka), va, (size_t)(ve - va)) && bad)
            (*bad)++;
    }
}

/* ------------------------------------------------------------ descoberta */

int bridge_parse_discovery(const char *buf, size_t len, uint16_t *http_port)
{
    static const char prefix[] = "CCPS4-BRIDGE v1 http=";
    const size_t pl = sizeof(prefix) - 1;
    size_t i = pl, start;
    uint64_t port;

    if (len < pl + 1 || memcmp(buf, prefix, pl) != 0)
        return 0;
    start = i;
    while (i < len && buf[i] >= '0' && buf[i] <= '9')
        i++;
    if (!parse_u64(buf + start, i - start, &port) || port < 1 || port > 65535)
        return 0;
    /* Depois da porta: fim, "\n", "\r\n" ou " campos-futuros". */
    if (i < len && buf[i] != '\n' && buf[i] != '\r' && buf[i] != ' ')
        return 0;
    *http_port = (uint16_t)port;
    return 1;
}

/* ------------------------------------------------------------ viewer.cfg */

void bridge_config_defaults(viewer_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->port = BRIDGE_DEFAULT_HTTP_PORT;
    cfg->udp_port = BRIDGE_DISCOVERY_PORT;
    cfg->utc_offset_min = -180;
    cfg->font_scale = 3;
}

static int cfg_kv(void *ctx, const char *k, size_t kn, const char *v, size_t vn)
{
    viewer_config_t *cfg = ctx;
    int64_t n;

    if (key_is(k, kn, "host")) {
        if (vn >= sizeof(cfg->host))
            return 0;
        for (size_t i = 0; i < vn; i++)
            if (!((v[i] >= '0' && v[i] <= '9') || v[i] == '.'))
                return 0; /* só IPv4 numérico (o app não usa DNS) */
        memcpy(cfg->host, v, vn);
        cfg->host[vn] = '\0';
        return 1;
    }
    if (key_is(k, kn, "port") || key_is(k, kn, "udp_port")) {
        if (!parse_i64(v, vn, &n) || n < 1 || n > 65535)
            return 0;
        if (k[0] == 'p')
            cfg->port = (uint16_t)n;
        else
            cfg->udp_port = (uint16_t)n;
        return 1;
    }
    if (key_is(k, kn, "utc_offset")) {
        if (!parse_i64(v, vn, &n) || n < -720 || n > 840)
            return 0;
        cfg->utc_offset_min = (int)n;
        return 1;
    }
    if (key_is(k, kn, "font_scale")) {
        if (!parse_i64(v, vn, &n) || (n != 2 && n != 3))
            return 0;
        cfg->font_scale = (int)n;
        return 1;
    }
    return 1; /* chave desconhecida: ignorada, não é erro */
}

typedef struct {
    viewer_config_t *cfg;
    int applied;
} cfg_ctx_t;

static int cfg_kv_count(void *ctx, const char *k, size_t kn, const char *v, size_t vn)
{
    cfg_ctx_t *c = ctx;
    int known = key_is(k, kn, "host") || key_is(k, kn, "port") || key_is(k, kn, "udp_port") ||
                key_is(k, kn, "utc_offset") || key_is(k, kn, "font_scale");
    int ok = cfg_kv(c->cfg, k, kn, v, vn);
    if (ok && known)
        c->applied++;
    return ok;
}

int bridge_parse_config(viewer_config_t *cfg, const char *text, size_t len, int *bad)
{
    cfg_ctx_t c = {cfg, 0};
    if (bad)
        *bad = 0;
    for_each_kv(text, len, cfg_kv_count, &c, bad);
    return c.applied;
}

/* ------------------------------------------------------------ /v1/feed */

static int parse_feed_line(const char *l, size_t n, uint64_t last_seq, feed_entry_t *e)
{
    const char *end = l + n, *t1, *t2, *t3, *text;
    size_t tl;
    int kind;

    t1 = memchr(l, '\t', n);
    if (!t1)
        return 0;
    t2 = memchr(t1 + 1, '\t', (size_t)(end - t1 - 1));
    if (!t2)
        return 0;
    t3 = memchr(t2 + 1, '\t', (size_t)(end - t2 - 1));
    if (!t3)
        return 0;
    if (!parse_u64(l, (size_t)(t1 - l), &e->seq) || e->seq == 0 || e->seq <= last_seq)
        return 0;
    if (!parse_i64(t1 + 1, (size_t)(t2 - t1 - 1), &e->ts))
        return 0;
    if (t3 == t2 + 1)
        return 0; /* tipo vazio */
    kind = bridge_kind_parse(t2 + 1, (size_t)(t3 - t2 - 1));
    e->kind = kind < 0 ? KIND_INFO : kind;

    text = t3 + 1;
    tl = utf8_truncate(text, (size_t)(end - text), BRIDGE_TEXT_MAX);
    for (size_t i = 0; i < tl; i++) {
        unsigned char c = (unsigned char)text[i];
        e->text[i] = (c < 0x20 || c == 0x7F) ? ' ' : (char)c;
    }
    e->text[tl] = '\0';
    e->text_len = (uint16_t)tl;
    return 1;
}

void bridge_parse_feed(const char *body, size_t len, feed_entry_cb cb, void *ctx, feed_stats_t *st)
{
    const char *p = body, *end = body + len;
    uint64_t last = 0;
    feed_entry_t e;

    memset(st, 0, sizeof(*st));
    while (p < end) {
        const char *nl = memchr(p, '\n', (size_t)(end - p));
        const char *le = nl ? nl : end;
        const char *ls = p;
        p = nl ? nl + 1 : end;
        if (le > ls && le[-1] == '\r')
            le--;
        if (le == ls)
            continue;
        st->lines++;
        if (!parse_feed_line(ls, (size_t)(le - ls), last, &e)) {
            st->malformed++;
            continue;
        }
        last = e.seq;
        st->max_seq = e.seq;
        st->accepted++;
        if (cb)
            cb(ctx, &e);
    }
}

/* ------------------------------------------------------------ /v1/state */

void bridge_state_reset(bridge_state_t *s)
{
    memset(s, 0, sizeof(*s));
    s->agent = AGENT_UNKNOWN;
    s->ps4debug = -1;
    s->attached_pid = -1;
    s->paused = -1;
    s->pending_events = -1;
    s->messages_pending = -1;
}

static int parse_bool(const char *v, size_t n)
{
    static const char *const yes[] = {"1", "true", "True", "TRUE", "yes", "sim"};
    static const char *const no[] = {"0", "false", "False", "FALSE", "no", "nao", "não"};
    for (size_t i = 0; i < sizeof(yes) / sizeof(yes[0]); i++)
        if (key_is(v, n, yes[i]))
            return 1;
    for (size_t i = 0; i < sizeof(no) / sizeof(no[0]); i++)
        if (key_is(v, n, no[i]))
            return 0;
    return -1;
}

static long parse_count(const char *v, size_t n)
{
    int64_t x;
    if (n == 0 || key_is(v, n, "None") || !parse_i64(v, n, &x) || x < 0 || x > 2147483647)
        return -1;
    return (long)x;
}

static void copy_str(char *dst, size_t cap, const char *v, size_t n)
{
    if (key_is(v, n, "None"))
        n = 0;
    n = utf8_truncate(v, n, cap - 1);
    memcpy(dst, v, n);
    dst[n] = '\0';
}

typedef struct {
    bridge_state_t *s;
    int known;
} state_ctx_t;

static int state_kv(void *ctx, const char *k, size_t kn, const char *v, size_t vn)
{
    state_ctx_t *c = ctx;
    bridge_state_t *s = c->s;

    if (key_is(k, kn, "agent")) {
        s->agent = key_is(v, vn, "idle")            ? AGENT_IDLE
                   : key_is(v, vn, "working")       ? AGENT_WORKING
                   : key_is(v, vn, "waiting_event") ? AGENT_WAITING_EVENT
                                                    : AGENT_UNKNOWN;
    } else if (key_is(k, kn, "ps4debug")) {
        s->ps4debug = key_is(v, vn, "connected") ? 1 : key_is(v, vn, "disconnected") ? 0 : parse_bool(v, vn);
    } else if (key_is(k, kn, "attached_pid")) {
        s->attached_pid = parse_count(v, vn);
    } else if (key_is(k, kn, "process")) {
        copy_str(s->process, sizeof(s->process), v, vn);
    } else if (key_is(k, kn, "paused")) {
        s->paused = parse_bool(v, vn);
    } else if (key_is(k, kn, "pending_events")) {
        s->pending_events = parse_count(v, vn);
    } else if (key_is(k, kn, "last_tool")) {
        copy_str(s->last_tool, sizeof(s->last_tool), v, vn);
    } else if (key_is(k, kn, "messages_pending")) {
        s->messages_pending = parse_count(v, vn);
    } else {
        return 1; /* chave desconhecida: ignorada */
    }
    c->known++;
    return 1;
}

int bridge_parse_state(const char *body, size_t len, bridge_state_t *s)
{
    state_ctx_t c = {s, 0};
    bridge_state_reset(s);
    for_each_kv(body, len, state_kv, &c, NULL);
    return c.known;
}

/* ------------------------------------------------------------ /v1/message */

int bridge_parse_ok(const char *body, size_t len, long *id)
{
    const char *a = body, *b = body + len;
    int64_t v;
    trim(&a, &b);
    while (b > a && b[-1] == '\n')
        b--;
    trim(&a, &b);
    if (b - a < 4 || memcmp(a, "ok ", 3) != 0)
        return 0;
    if (!parse_i64(a + 3, (size_t)(b - a - 3), &v) || v < 0)
        return 0;
    *id = (long)v;
    return 1;
}

const char *bridge_check_message(const char *msg, size_t len)
{
    size_t i;
    if (len > BRIDGE_MSG_MAX)
        return "mensagem maior que 512 bytes";
    for (i = 0; i < len; i++)
        if (msg[i] != ' ' && msg[i] != '\t' && msg[i] != '\n' && msg[i] != '\r')
            break;
    if (i == len)
        return "mensagem vazia";
    if (!utf8_valid(msg, len))
        return "texto com UTF-8 inválido";
    return 0;
}
