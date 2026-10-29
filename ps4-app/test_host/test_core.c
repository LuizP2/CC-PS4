/*
 * Testes unitários do núcleo (rodam no host com gcc + ASan/UBSan):
 * parser HTTP, parsers do protocolo (feed/state/descoberta/config/ok),
 * UTF-8/UTF-16, quebra de linha, fonte e desenho.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/core/bridge.h"
#include "../src/core/font.h"
#include "../src/core/http.h"
#include "../src/core/render.h"
#include "../src/core/utf8.h"
#include "../src/core/viewer.h"
#include "../src/core/wrap.h"
#include "../src/platform.h"
#include "../src/platform_host.h"

static int g_fail, g_pass;

#define CHECK(cond)                                                                 \
    do {                                                                            \
        if (cond) {                                                                 \
            g_pass++;                                                               \
        } else {                                                                    \
            g_fail++;                                                               \
            fprintf(stderr, "FALHOU %s:%d: %s\n", __FILE__, __LINE__, #cond);       \
        }                                                                           \
    } while (0)

/* ------------------------------------------------------------ HTTP */

static char body[4096];

static int feed_all(http_resp_t *r, const char *s, size_t chunk)
{
    size_t len = strlen(s);
    int rc = HTTP_NEED_MORE;
    for (size_t i = 0; i < len && rc == HTTP_NEED_MORE; i += chunk) {
        size_t n = (len - i < chunk) ? len - i : chunk;
        rc = http_resp_feed(r, s + i, n);
    }
    return rc;
}

static void test_http(void)
{
    http_resp_t r;
    const char *ok = "HTTP/1.0 200 OK\r\nServer: x\r\nContent-Type: text/plain; charset=utf-8\r\n"
                     "Content-Length: 5\r\nConnection: close\r\n\r\nhello";
    char req[512];
    int n;

    /* resposta completa, inteira e byte a byte */
    for (size_t chunk = 1; chunk <= 64; chunk = chunk * 2 + 1) {
        http_resp_init(&r, body, sizeof(body));
        CHECK(feed_all(&r, ok, chunk) == HTTP_COMPLETE);
        CHECK(r.status == 200 && r.content_length == 5 && r.body_len == 5 && memcmp(body, "hello", 5) == 0);
        CHECK(http_resp_finish(&r) == HTTP_COMPLETE);
    }

    /* bytes além do Content-Length são ignorados */
    http_resp_init(&r, body, sizeof(body));
    CHECK(feed_all(&r, "HTTP/1.1 200 OK\nContent-Length: 2\n\nabEXTRA", 1000) == HTTP_COMPLETE);
    CHECK(r.body_len == 2 && r.extra_bytes == 5);

    /* só LF, nome do cabeçalho em outra caixa, sem frase de motivo */
    http_resp_init(&r, body, sizeof(body));
    CHECK(feed_all(&r, "HTTP/1.0 400\ncOnTeNt-LeNgTh:   3  \n\nbad", 1000) == HTTP_COMPLETE);
    CHECK(r.status == 400 && r.body_len == 3);

    /* Content-Length 0 completa já no fim dos cabeçalhos */
    http_resp_init(&r, body, sizeof(body));
    CHECK(feed_all(&r, "HTTP/1.0 204 No Content\r\nContent-Length: 0\r\n\r\n", 1000) == HTTP_COMPLETE);

    /* sem Content-Length: corpo até o EOF (HTTP/1.0) */
    http_resp_init(&r, body, sizeof(body));
    CHECK(feed_all(&r, "HTTP/1.0 200 OK\r\n\r\nline1\nline2\n", 3) == HTTP_NEED_MORE);
    CHECK(r.content_length == -1);
    CHECK(http_resp_finish(&r) == HTTP_COMPLETE);
    CHECK(r.body_len == 12 && memcmp(body, "line1\nline2\n", 12) == 0);

    /* sem Content-Length e corpo maior que o buffer -> erro */
    {
        char small[4];
        http_resp_init(&r, small, sizeof(small));
        CHECK(feed_all(&r, "HTTP/1.0 200 OK\r\n\r\n12345", 1000) == HTTP_ERROR);
    }
    /* Content-Length maior que o buffer -> erro já nos cabeçalhos */
    {
        char small[4];
        http_resp_init(&r, small, sizeof(small));
        CHECK(feed_all(&r, "HTTP/1.0 200 OK\r\nContent-Length: 99\r\n\r\n", 1000) == HTTP_ERROR);
        CHECK(r.error != NULL);
    }

    /* corpo truncado: EOF antes do Content-Length */
    http_resp_init(&r, body, sizeof(body));
    CHECK(feed_all(&r, "HTTP/1.0 200 OK\r\nContent-Length: 10\r\n\r\nabc", 1000) == HTTP_NEED_MORE);
    CHECK(http_resp_finish(&r) == HTTP_ERROR);

    /* EOF no meio dos cabeçalhos / resposta vazia */
    http_resp_init(&r, body, sizeof(body));
    CHECK(feed_all(&r, "HTTP/1.0 200 OK\r\nContent-Le", 1000) == HTTP_NEED_MORE);
    CHECK(http_resp_finish(&r) == HTTP_ERROR);
    http_resp_init(&r, body, sizeof(body));
    CHECK(http_resp_finish(&r) == HTTP_ERROR);

    /* linhas de status inválidas */
    const char *bad_status[] = {"FTP/1.0 200 OK\r\n", "HTTP/1.0 2x0 OK\r\n", "HTTP/1.0 200OK\r\n",
                                "HTTP/x.0 200 OK\r\n", "garbage\n", "HTTP/1.0 099 X\r\n"};
    for (size_t i = 0; i < sizeof(bad_status) / sizeof(bad_status[0]); i++) {
        http_resp_init(&r, body, sizeof(body));
        CHECK(feed_all(&r, bad_status[i], 1000) == HTTP_ERROR);
    }

    /* Content-Length inválido, duplicado diferente, chunked, cabeçalho sem ':' */
    const char *bad_hdr[] = {
        "HTTP/1.0 200 OK\r\nContent-Length: -1\r\n\r\n",
        "HTTP/1.0 200 OK\r\nContent-Length: 1x\r\n\r\n",
        "HTTP/1.0 200 OK\r\nContent-Length:\r\n\r\n",
        "HTTP/1.0 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n",
        "HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
        "HTTP/1.0 200 OK\r\nSem dois pontos\r\n\r\n",
        "HTTP/1.0 200 OK\r\nBad Name: x\r\n\r\n",
    };
    for (size_t i = 0; i < sizeof(bad_hdr) / sizeof(bad_hdr[0]); i++) {
        http_resp_init(&r, body, sizeof(body));
        CHECK(feed_all(&r, bad_hdr[i], 7) == HTTP_ERROR);
    }
    /* Content-Length duplicado mas igual é aceito */
    http_resp_init(&r, body, sizeof(body));
    CHECK(feed_all(&r, "HTTP/1.0 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nZ", 1000) ==
          HTTP_COMPLETE);

    /* cabeçalho gigante */
    {
        static char big[HTTP_LINE_MAX + 64];
        memset(big, 'a', sizeof(big) - 1);
        big[sizeof(big) - 1] = '\0';
        memcpy(big, "HTTP/1.0 200 OK\r\nX: ", 20);
        http_resp_init(&r, body, sizeof(body));
        CHECK(feed_all(&r, big, 100) == HTTP_ERROR);
    }

    /* depois de erro, continua em erro */
    CHECK(http_resp_feed(&r, "x", 1) == HTTP_ERROR);

    /* montagem de requisições */
    n = http_build_get(req, sizeof(req), "192.168.0.10:8765", "/v1/feed?since=42");
    CHECK(n > 0 && strncmp(req, "GET /v1/feed?since=42 HTTP/1.0\r\n", 32) == 0);
    CHECK(strstr(req, "\r\nHost: 192.168.0.10:8765\r\n") && strstr(req, "Connection: close\r\n\r\n"));
    CHECK((size_t)n == strlen(req) && strcmp(req + n - 4, "\r\n\r\n") == 0);
    n = http_build_post(req, sizeof(req), "h:1", "/v1/message", "não", 4);
    CHECK(n > 0 && strstr(req, "Content-Length: 4\r\n") && strcmp(req + n - 4, "não") == 0);
    CHECK(strstr(req, "POST /v1/message HTTP/1.0\r\n") == req);
    CHECK(http_build_get(req, 10, "h", "/v1/state") == -1);
}

/* ------------------------------------------------------------ protocolo */

typedef struct {
    feed_entry_t e[16];
    int n;
} collect_t;

static void collect(void *ctx, const feed_entry_t *e)
{
    collect_t *c = ctx;
    if (c->n < 16)
        c->e[c->n++] = *e;
}

static void test_feed(void)
{
    collect_t c = {.n = 0};
    feed_stats_t st;
    const char *b = "1\t1700000000\ttool\tset_breakpoint(eboot.bin+0x20)\n"
                    "2\t1700000001\tresult\tbreakpoint #1 em 0x420020\r\n"
                    "\n"
                    "lixo sem tabs\n"
                    "x\t1\tinfo\tseq não numérico\n"
                    "0\t1\tinfo\tseq zero\n"
                    "3\tabc\tinfo\tts inválido\n"
                    "4\t1700000002\t\ttipo vazio\n"
                    "5\t1700000003.25\tevent\tcrash em crash_here+0x18: Olá, ação, coração!\n"
                    "5\t1700000004\tinfo\tseq repetido\n"
                    "4\t1700000004\tinfo\tseq fora de ordem\n"
                    "6\t-5\tnovo_tipo\ttipo desconhecido vira info\n"
                    "7\t1700000005\tuser\ttexto\tcom\ttabs\n"
                    "8\t1700000006\terror\t\n"
                    "9\t1700000007\tstatus\tsem newline no fim";
    bridge_parse_feed(b, strlen(b), collect, &c, &st);
    CHECK(st.accepted == 7 && c.n == 7);
    CHECK(st.malformed == 7);
    CHECK(st.max_seq == 9);
    CHECK(c.e[0].seq == 1 && c.e[0].kind == KIND_TOOL && strcmp(c.e[0].text, "set_breakpoint(eboot.bin+0x20)") == 0);
    CHECK(c.e[1].kind == KIND_RESULT && strcmp(c.e[1].text, "breakpoint #1 em 0x420020") == 0); /* \r removido */
    CHECK(c.e[2].seq == 5 && c.e[2].ts == 1700000003 && c.e[2].kind == KIND_EVENT);
    CHECK(strstr(c.e[2].text, "Olá, ação, coração!") != NULL);
    CHECK(c.e[3].seq == 6 && c.e[3].kind == KIND_INFO && c.e[3].ts == -5);
    CHECK(strcmp(c.e[4].text, "texto com tabs") == 0);
    CHECK(c.e[5].kind == KIND_ERROR && c.e[5].text_len == 0);
    CHECK(c.e[6].seq == 9 && strcmp(c.e[6].text, "sem newline no fim") == 0);

    /* texto > 300 bytes cortado sem quebrar UTF-8 ("ç" = 2 bytes) */
    {
        static char big[1024];
        size_t o = (size_t)snprintf(big, sizeof(big), "10\t1\tinfo\tx");
        for (int i = 0; i < 200; i++) {
            memcpy(big + o, "ç", 2);
            o += 2;
        }
        big[o++] = '\n';
        c.n = 0;
        bridge_parse_feed(big, o, collect, &c, &st);
        CHECK(c.n == 1 && c.e[0].text_len == 299 && utf8_valid(c.e[0].text, c.e[0].text_len));
    }
    /* corpo vazio */
    c.n = 0;
    bridge_parse_feed("", 0, collect, &c, &st);
    CHECK(c.n == 0 && st.lines == 0 && st.max_seq == 0);
    /* caracteres de controle no texto viram espaço; UTF-8 inválido é mantido (render mostra '?') */
    c.n = 0;
    {
        const char *ctl = "11\t1\tinfo\ta\x01" "b\xff" "c\n";
        bridge_parse_feed(ctl, strlen(ctl), collect, &c, &st);
    }
    CHECK(c.n == 1 && memcmp(c.e[0].text, "a b\xff" "c", 5) == 0);

    CHECK(bridge_kind_parse("event", 5) == KIND_EVENT && bridge_kind_parse("Event", 5) == -1);
    CHECK(strcmp(bridge_kind_name(KIND_USER), "user") == 0 && strcmp(bridge_kind_name(99), "info") == 0);
}

static void test_state(void)
{
    bridge_state_t s;
    const char *b = "agent=waiting_event\nps4debug=connected\r\nattached_pid=87\nprocess=eboot.bin\n"
                    "paused=1\npending_events=2\nlast_tool=wait_for_event\nmessages_pending=0\n"
                    "chave_nova=ignorada\nlinha sem igual\n";
    CHECK(bridge_parse_state(b, strlen(b), &s) == 8);
    CHECK(s.agent == AGENT_WAITING_EVENT && s.ps4debug == 1 && s.attached_pid == 87);
    CHECK(strcmp(s.process, "eboot.bin") == 0 && s.paused == 1 && s.pending_events == 2);
    CHECK(strcmp(s.last_tool, "wait_for_event") == 0 && s.messages_pending == 0);

    b = "agent=idle\nps4debug=disconnected\nattached_pid=\nprocess=\npaused=False\npending_events=x\n"
        "last_tool=None\n";
    bridge_parse_state(b, strlen(b), &s);
    CHECK(s.agent == AGENT_IDLE && s.ps4debug == 0 && s.attached_pid == -1 && s.process[0] == '\0');
    CHECK(s.paused == 0 && s.pending_events == -1 && s.last_tool[0] == '\0' && s.messages_pending == -1);

    CHECK(bridge_parse_state("", 0, &s) == 0 && s.agent == AGENT_UNKNOWN && s.paused == -1);
    bridge_parse_state("agent=dançando\npaused=talvez\n", 29, &s);
    CHECK(s.agent == AGENT_UNKNOWN && s.paused == -1);
}

static void test_discovery_config_ok(void)
{
    uint16_t port = 0;
    viewer_config_t cfg;
    int bad = 0;
    long id = 0;
    const char *ok = "CCPS4-BRIDGE v1 http=8765\n";

    CHECK(bridge_parse_discovery(ok, strlen(ok), &port) == 1 && port == 8765);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v1 http=1", 22, &port) == 1 && port == 1);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v1 http=9000\r\n", 27, &port) == 1 && port == 9000);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v1 http=9001 futuro=1\n", 35, &port) == 1 && port == 9001);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v1 http=0\n", 23, &port) == 0);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v1 http=65536\n", 27, &port) == 0);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v2 http=8765\n", 26, &port) == 0);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v1 http=\n", 22, &port) == 0);
    CHECK(bridge_parse_discovery("CCPS4-BRIDGE v1 http=87x5\n", 26, &port) == 0);
    CHECK(bridge_parse_discovery("", 0, &port) == 0);

    bridge_config_defaults(&cfg);
    CHECK(cfg.port == 8765 && cfg.udp_port == 8766 && cfg.host[0] == '\0' && cfg.font_scale == 3);
    const char *c1 = "# comentário\r\nhost = 192.168.0.10 \r\nport=9000\nudp_port=18766\n"
                     "utc_offset=-120\nfont_scale=2\ndesconhecida=1\nport=abc\nhost=pc.local\nlixo\n";
    CHECK(bridge_parse_config(&cfg, c1, strlen(c1), &bad) == 5);
    CHECK(bad == 3);
    CHECK(strcmp(cfg.host, "192.168.0.10") == 0 && cfg.port == 9000 && cfg.udp_port == 18766);
    CHECK(cfg.utc_offset_min == -120 && cfg.font_scale == 2);

    CHECK(bridge_parse_ok("ok 12\n", 6, &id) == 1 && id == 12);
    CHECK(bridge_parse_ok("ok 7", 4, &id) == 1 && id == 7);
    CHECK(bridge_parse_ok("ok\n", 3, &id) == 0);
    CHECK(bridge_parse_ok("erro 1\n", 7, &id) == 0);

    CHECK(bridge_check_message("continue", 8) == NULL);
    CHECK(bridge_check_message("", 0) != NULL);
    CHECK(bridge_check_message("  \n", 3) != NULL);
    CHECK(bridge_check_message("\xc3", 1) != NULL);
    {
        char big[600];
        memset(big, 'a', sizeof(big));
        CHECK(bridge_check_message(big, 512) == NULL && bridge_check_message(big, 513) != NULL);
    }
}

/* ------------------------------------------------------------ UTF-8 */

static void test_utf8(void)
{
    const char *s = "Olá, ação, coração!";
    size_t pos = 0;
    uint32_t cp, cps[32];
    int n = 0, r;
    uint16_t u16[64];
    char back[64];

    while ((r = utf8_next(s, strlen(s), &pos, &cp)) > 0)
        cps[n++] = cp;
    CHECK(r == -1 && n == 19);
    CHECK(cps[2] == 0xE1 && cps[6] == 0xE7 && cps[7] == 0xE3 && cps[16] == 0xE3);
    CHECK(utf8_count(s, strlen(s)) == 19 && utf8_valid(s, strlen(s)));

    /* 3 e 4 bytes */
    pos = 0;
    CHECK(utf8_next("\xe2\x82\xac", 3, &pos, &cp) == 1 && cp == 0x20AC && pos == 3);
    pos = 0;
    CHECK(utf8_next("\xf0\x9f\x98\x80", 4, &pos, &cp) == 1 && cp == 0x1F600 && pos == 4);

    /* inválidos: overlong, surrogate, > U+10FFFF, continuação solta, truncada, 0xFF */
    const char *bad[] = {"\xc0\x80", "\xe0\x80\x80", "\xed\xa0\x80", "\xf4\x90\x80\x80",
                         "\x80", "\xc3", "\xe2\x82", "\xff", "\xc3\x28"};
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        pos = 0;
        CHECK(utf8_next(bad[i], strlen(bad[i]), &pos, &cp) == 0 && cp == UTF8_REPLACEMENT && pos == 1);
        CHECK(!utf8_valid(bad[i], strlen(bad[i])));
    }
    CHECK(utf8_count("\xc3(", 2) == 2); /* inválido conta 1 coluna e ressincroniza */

    /* truncamento sem cortar caractere */
    CHECK(utf8_truncate("aç", 3, 2) == 1);
    CHECK(utf8_truncate("aç", 3, 3) == 3);
    CHECK(utf8_truncate("a\xf0\x9f\x98\x80", 5, 4) == 1);
    CHECK(utf8_truncate("abc", 3, 10) == 3);

    /* UTF-16 <-> UTF-8 (caminho do teclado do PS4) */
    size_t un = utf8_to_utf16("ação 😀", strlen("ação 😀"), u16, 64);
    CHECK(un == 7 && u16[1] == 0xE7 && u16[5] == 0xD83D && u16[6] == 0xDE00 && u16[7] == 0);
    CHECK(utf16_to_utf8(u16, 64, back, sizeof(back)) == strlen("ação 😀") && strcmp(back, "ação 😀") == 0);
    {
        const uint16_t lone[] = {'a', 0xD800, 'b', 0xDC00, 0};
        CHECK(utf16_to_utf8(lone, 5, back, sizeof(back)) == 4 && strcmp(back, "a?b?") == 0);
        const uint16_t acc[] = {0xE7, 0xE7, 0xE7, 0};
        CHECK(utf16_to_utf8(acc, 4, back, 6) == 4 && strcmp(back, "çç") == 0); /* não corta no meio */
    }
    CHECK(utf8_to_utf16("abc", 3, u16, 2) == 1 && u16[1] == 0);
}

/* ------------------------------------------------------------ quebra de linha */

static int span_eq(const char *s, wrap_span_t sp, const char *want)
{
    return sp.end - sp.start == strlen(want) && memcmp(s + sp.start, want, strlen(want)) == 0;
}

static void test_wrap(void)
{
    wrap_span_t sp[16];
    const char *s = "breakpoint em crash_here atingido";
    int n = wrap_utf8(s, strlen(s), 14, sp, 16);
    CHECK(n == 3);
    CHECK(span_eq(s, sp[0], "breakpoint em") && span_eq(s, sp[1], "crash_here") && span_eq(s, sp[2], "atingido"));

    /* acentos contam 1 coluna por caractere */
    s = "ação coração";
    n = wrap_utf8(s, strlen(s), 7, sp, 16);
    CHECK(n == 2 && span_eq(s, sp[0], "ação") && span_eq(s, sp[1], "coração"));

    /* palavra longa é cortada à força */
    s = "0123456789ABCDEF";
    n = wrap_utf8(s, strlen(s), 6, sp, 16);
    CHECK(n == 3 && span_eq(s, sp[0], "012345") && span_eq(s, sp[2], "CDEF"));

    /* cabe exatamente; vazio; espaços múltiplos; \n forçado */
    s = "abcdef";
    CHECK(wrap_utf8(s, 6, 6, sp, 16) == 1 && span_eq(s, sp[0], "abcdef"));
    CHECK(wrap_utf8("", 0, 10, sp, 16) == 1 && sp[0].start == sp[0].end);
    s = "aa    bb";
    n = wrap_utf8(s, strlen(s), 3, sp, 16);
    CHECK(n == 2 && span_eq(s, sp[1], "bb"));
    s = "linha1\nlinha2";
    n = wrap_utf8(s, strlen(s), 40, sp, 16);
    CHECK(n == 2 && span_eq(s, sp[0], "linha1") && span_eq(s, sp[1], "linha2"));
    s = "fim com espaços   ";
    CHECK(wrap_utf8(s, strlen(s), 15, sp, 16) == 1);

    /* out == NULL só conta; max_out menor que o total */
    s = "a b c d e f g h";
    CHECK(wrap_utf8(s, strlen(s), 1, NULL, 0) == 8);
    CHECK(wrap_utf8(s, strlen(s), 1, sp, 2) == 8 && span_eq(s, sp[1], "b"));
    CHECK(wrap_utf8("abc", 3, 0, sp, 16) == 3); /* cols < 1 vira 1 */

    /* nenhuma linha passa da largura (texto real com acentos) */
    s = "o agente encontrou a função crash_here: leitura de p->value com p == NULL (endereço 0x8)";
    n = wrap_utf8(s, strlen(s), 20, sp, 16);
    int ok = 1;
    for (int i = 0; i < n; i++)
        if (utf8_count(s + sp[i].start, sp[i].end - sp[i].start) > 20)
            ok = 0;
    CHECK(ok && n == 5);
}

/* ------------------------------------------------------------ fonte e desenho */

static void test_font_render(void)
{
    int fb;
    const uint8_t *q = font_glyph('?', &fb);
    CHECK(font_glyph('A', &fb)[0] == 0x0C && fb == 0);
    CHECK(font_glyph(0xE7, &fb) != q && fb == 0);  /* ç */
    CHECK(font_glyph(0xE3, &fb) != q && fb == 0);  /* ã */
    CHECK(font_glyph(0xA0, &fb) != NULL && fb == 0);
    CHECK(font_glyph(0xFF, &fb) != q && fb == 0);
    CHECK(font_glyph(0x4E2D, &fb) == q && fb == 1); /* 中 -> '?' */
    CHECK(font_glyph(0x1F600, &fb) == q && fb == 1);
    CHECK(font_glyph(0x80, &fb) == q && fb == 1);   /* C1 */
    CHECK(font_glyph(0x2715, &fb) != q && fb == 0); /* ✕ */
    CHECK(font_glyph(0x201C, &fb) == font_glyph('"', NULL));

    static uint32_t px[64 * 32];
    canvas_t c = {px, 64, 32, 64};
    canvas_fill(&c, 0xFF000000u);
    int x = canvas_text(&c, 0, 0, "ç", 2, 1, 0xFFFFFFFFu, -1);
    CHECK(x == 8);
    int lit = 0;
    for (int i = 0; i < 64 * 32; i++)
        lit += px[i] == 0xFFFFFFFFu;
    CHECK(lit > 5);
    /* recorte nas bordas não escreve fora do buffer (ASan pegaria) */
    canvas_text(&c, -20, -5, "borda", 5, 3, 0xFFFF0000u, -1);
    canvas_text(&c, 60, 28, "borda", 5, 3, 0xFFFF0000u, -1);
    canvas_rect(&c, -10, -10, 1000, 1000, 0xFF00FF00u);
    CHECK(px[0] == 0xFF00FF00u && px[64 * 32 - 1] == 0xFF00FF00u);
    /* max_cols corta com reticências */
    canvas_fill(&c, 0);
    CHECK(canvas_text(&c, 0, 0, "abcdefgh", 8, 1, 0xFFFFFFFFu, 4) == 32);

    char hms[9];
    ui_format_hms(0, 0, hms);
    CHECK(strcmp(hms, "00:00:00") == 0);
    ui_format_hms(3600 + 61, -180, hms);
    CHECK(strcmp(hms, "22:01:01") == 0);
    ui_format_hms(1700000000, -180, hms); /* 2023-11-14 22:13:20 UTC */
    CHECK(strcmp(hms, "19:13:20") == 0);
}

/* ------------------------------------------------------------ viewer offline */

static void test_viewer_offline(void)
{
    static viewer_t v;
    viewer_config_t cfg;
    static uint32_t px[1920 * 1080];
    canvas_t c = {px, 1920, 1080, 1920};

    bridge_config_defaults(&cfg);
    snprintf(cfg.host, sizeof(cfg.host), "127.0.0.1");
    cfg.port = 1; /* porta fechada: conexão recusada */
    viewer_init(&v, &cfg, 1000);
    CHECK(v.conn == CONN_CONNECTING && v.host_configured);

    /* sem conexão, enviar mostra aviso e não enfileira */
    CHECK(viewer_send(&v, "continue", 8, 1000) < 0 && v.toast_kind == TOAST_ERROR && !v.out_pending);

    /* frases: ← → dão a volta na lista */
    viewer_input(&v, VB_LEFT, 1000);
    CHECK(v.phrase == viewer_phrase_count - 1);
    viewer_input(&v, 0, 1010);
    viewer_input(&v, VB_RIGHT, 1020);
    CHECK(v.phrase == 0);
    viewer_input(&v, 0, 1030);

    /* teclado indisponível */
    host_ime_script(0, NULL, 0);
    viewer_input(&v, VB_TRIANGLE, 1040);
    CHECK(!v.ime_active && strstr(v.toast, "teclado indisponível") != NULL);
    viewer_input(&v, 0, 1050);

    /* conexão recusada -> OFFLINE com nova tentativa em 3 s */
    for (uint64_t t = 1100; t < 4000 && v.conn != CONN_OFFLINE; t += 10)
        viewer_tick(&v, t);
    CHECK(v.conn == CONN_OFFLINE && v.last_error[0] != '\0');

    /* rolagem com muitas linhas locais; nunca passa do topo nem do fim */
    for (int i = 0; i < 60; i++)
        viewer_add_local(&v, KIND_INFO, "linha local %d com texto para preencher a tela", i);
    ui_render(&c, &v, 5000);
    CHECK(v.feed_rows > 10 && v.feed_total_lines >= 60);
    for (int i = 0; i < 500; i++) {
        viewer_input(&v, VB_UP, 6000 + (uint64_t)i * 100);
        viewer_input(&v, 0, 6000 + (uint64_t)i * 100 + 50);
    }
    CHECK(v.scroll == v.feed_total_lines - v.feed_rows);
    viewer_input(&v, VB_CIRCLE, 70000);
    CHECK(v.scroll == 0);
    viewer_shutdown(&v);
}

int main(void)
{
    setenv("CCPS4_QUIET", "1", 1);
    net_init();
    test_http();
    test_feed();
    test_state();
    test_discovery_config_ok();
    test_utf8();
    test_wrap();
    test_font_render();
    test_viewer_offline();
    printf("test_core: %d verificações ok, %d falha(s)\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
