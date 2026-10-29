/*
 * Teste ponta a ponta no host: o núcleo do viewer + net_bsd.c + platform_host.c
 * conversando com test_host/bridge_stub.py por UDP (descoberta) e HTTP.
 *
 * Roteiro: descobre o PC -> recebe state e feed -> teclado indisponível ->
 * envia a frase pronta nº 3 com ✕ -> envia texto do "teclado" com △ ->
 * espera o eco "você: ..." no feed -> salva quadros PPM (online, rolado) ->
 * pede ao stub para desligar -> espera "desconectado" -> salva o quadro offline.
 *
 * Uso: e2e_main --udp-port N --out-dir DIR [--expect-malformed]
 */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../src/core/render.h"
#include "../src/core/viewer.h"
#include "../src/platform.h"
#include "../src/platform_host.h"

#define W 1920
#define H 1080

static viewer_t g_v;
static canvas_t g_c;
static int g_fail;

static void sleep_ms(int ms)
{
    struct timespec ts = {ms / 1000, (long)(ms % 1000) * 1000000L};
    nanosleep(&ts, NULL);
}

/* Roda o laço do app (tick + desenho) até cond() ou timeout. */
static int run_until(int (*cond)(void), int timeout_ms)
{
    uint64_t end = plat_now_ms() + (uint64_t)timeout_ms, last_draw = 0;
    while (plat_now_ms() < end) {
        uint64_t now = plat_now_ms();
        viewer_tick(&g_v, now);
        if (g_v.dirty && now - last_draw >= 50) {
            ui_render(&g_c, &g_v, now);
            g_v.dirty = 0;
            last_draw = now;
        }
        if (cond())
            return 1;
        sleep_ms(5);
    }
    return 0;
}

static void press(uint32_t b)
{
    uint64_t now = plat_now_ms();
    viewer_input(&g_v, b, now);
    viewer_input(&g_v, 0, now + 1);
}

static void expect(int ok, const char *what)
{
    printf("[e2e] %-58s %s\n", what, ok ? "ok" : "FALHOU");
    if (!ok)
        g_fail++;
}

static int c_online(void) { return g_v.conn == CONN_ONLINE && g_v.have_state && g_v.max_seq >= 20; }
static int c_sent1(void) { return g_v.sent_ok >= 1; }
static int c_sent2(void) { return g_v.sent_ok >= 2; }
static int c_echo(void) { return g_v.max_seq >= 22; }
static int c_offline(void) { return g_v.conn == CONN_OFFLINE; }
static int c_never(void) { return 0; }

static int feed_has(const char *needle)
{
    for (int i = 0; i < g_v.feed.count; i++)
        if (strstr(viewer_feed_at(&g_v, i)->text, needle))
            return 1;
    return 0;
}

static void save(const char *dir, const char *name)
{
    char path[512];
    ui_render(&g_c, &g_v, plat_now_ms());
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    expect(host_write_ppm(&g_c, path) == 0, path);
}

static void stop_stub(void)
{
    httpc_t *h = malloc(sizeof(*h));
    char req[256];
    int n = http_build_post(req, sizeof(req), g_v.pc_label, "/test/stop", "x", 1);
    uint64_t end = plat_now_ms() + 3000;
    int r = HTTPC_FAIL;

    httpc_init(h);
    if (n > 0 && httpc_start(h, g_v.pc_ip, g_v.pc_port, req, (size_t)n, plat_now_ms(), 2000, 3000) == HTTPC_PENDING)
        while ((r = httpc_step(h, plat_now_ms())) == HTTPC_PENDING && plat_now_ms() < end)
            sleep_ms(5);
    expect(r == HTTPC_OK && h->resp.status == 200, "POST /test/stop (desliga o stub)");
    httpc_abort(h);
    free(h);
}

int main(int argc, char **argv)
{
    viewer_config_t cfg;
    const char *out_dir = ".";
    int expect_malformed = 0;
    static const char ime_text[] = "teste pelo teclado: ação, coração e “aspas” ✓";

    bridge_config_defaults(&cfg);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--udp-port") && i + 1 < argc)
            cfg.udp_port = (uint16_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--out-dir") && i + 1 < argc)
            out_dir = argv[++i];
        else if (!strcmp(argv[i], "--expect-malformed"))
            expect_malformed = 1;
        else if (!strcmp(argv[i], "--scale") && i + 1 < argc)
            cfg.font_scale = atoi(argv[++i]);
    }
    net_init();
    g_c.px = calloc((size_t)W * H, sizeof(uint32_t));
    g_c.w = W;
    g_c.h = H;
    g_c.stride = W;
    viewer_init(&g_v, &cfg, plat_now_ms());

    /* 1. descoberta + primeiro ciclo state/feed */
    expect(run_until(c_online, 15000), "descobriu o PC por UDP e recebeu state + feed");
    printf("[e2e] PC: %s  seq máx: %llu  entradas: %d\n", g_v.pc_label,
           (unsigned long long)g_v.max_seq, g_v.feed.count);
    expect(g_v.state.agent == AGENT_WAITING_EVENT && g_v.state.attached_pid == 87 &&
               g_v.state.paused == 1 && strcmp(g_v.state.process, "eboot.bin") == 0,
           "state: waiting_event, pid 87, eboot.bin, pausado");
    expect(feed_has("Olá, ação"), "feed com acentos chegou inteiro");
    if (expect_malformed)
        expect(g_v.malformed_total > 0, "linhas malformadas do feed foram ignoradas");

    /* 2. teclado indisponível não derruba nada */
    host_ime_script(0, NULL, 0);
    press(VB_TRIANGLE);
    expect(strstr(g_v.toast, "teclado indisponível") != NULL, "△ sem teclado -> aviso 'teclado indisponível'");

    /* 3. frase pronta nº 3 com ✕ */
    press(VB_RIGHT);
    press(VB_RIGHT);
    expect(strcmp(viewer_phrases[g_v.phrase], "pode retomar o jogo (resume)") == 0, "→→ seleciona a frase 3");
    press(VB_CROSS);
    expect(run_until(c_sent1, 5000), "✕ enviou a frase (200 ok <id>)");
    expect(strstr(g_v.toast, "Enviado") != NULL, "aviso de confirmação após o envio");

    /* 4. texto livre pelo "teclado" (△) */
    host_ime_script(1, ime_text, 0);
    press(VB_TRIANGLE);
    expect(g_v.ime_active == 1, "△ abriu o teclado");
    expect(run_until(c_sent2, 5000), "texto do teclado enviado");
    viewer_input(&g_v, 0, plat_now_ms()); /* solta os botões (trava pós-teclado) */

    /* 5. o eco "você: ..." volta pelo feed */
    expect(run_until(c_echo, 5000), "eco das 2 mensagens apareceu no feed");
    expect(feed_has("você: pode retomar o jogo (resume)") && feed_has("ação, coração e “aspas”"),
           "feed contém as mensagens enviadas (UTF-8 intacto)");
    run_until(c_never, 300);
    save(out_dir, "viewer_online.ppm");

    /* 6. rolagem */
    for (int i = 0; i < 8; i++)
        press(VB_UP);
    expect(g_v.scroll == 8, "↑ x8 rola 8 linhas");
    save(out_dir, "viewer_scrolled.ppm");
    press(VB_CIRCLE);
    expect(g_v.scroll == 0, "○ volta ao fim");

    /* 7. PC some -> desconectado, e enviar é recusado com aviso */
    stop_stub();
    expect(run_until(c_offline, 8000), "detectou PC fora do ar (desconectado)");
    press(VB_CROSS);
    expect(strstr(g_v.toast, "PC desconectado") != NULL, "✕ offline -> 'Não enviado: PC desconectado'");
    run_until(c_never, 1200);
    save(out_dir, "viewer_offline.ppm");

    viewer_shutdown(&g_v);
    free(g_c.px);
    printf("[e2e] %s (%d falha(s))\n", g_fail ? "FALHOU" : "OK", g_fail);
    return g_fail ? 1 : 0;
}
